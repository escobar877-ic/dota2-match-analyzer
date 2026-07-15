from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models import Match, Team, TeamRating
from app.ratings.elo import (
    STARTING_ELO,
    format_multiplier,
    k_factor,
    rating_feature_value,
    recency_multiplier,
    uncertainty,
    update_elo,
)
from app.ratings.glicko import GlickoState, update_glicko_like
from app.ratings.schemas import EloState, TeamRatingRead
from app.ratings.team_identity import (
    SYNTHETIC_TEAM_SOURCES,
    build_team_identity_index,
)


RATING_TYPE = "elo"
GLICKO_RATING_TYPE = "glicko"


def recalculate_elo_ratings(db: Session) -> dict[str, int | str]:
    TeamA = aliased(Team)
    TeamB = aliased(Team)
    base_conditions = (
        Match.status == "finished",
        Match.winner_team_id.is_not(None),
        Match.is_tier1_match.is_(True),
        TeamA.is_active_tier1.is_(True),
        TeamB.is_active_tier1.is_(True),
    )
    real_match_count = db.scalar(
        select(func.count(Match.id))
        .join(TeamA, Match.team_a_id == TeamA.id)
        .join(TeamB, Match.team_b_id == TeamB.id)
        .where(
            *base_conditions,
            or_(Match.external_source.is_(None), Match.external_source.not_in(SYNTHETIC_TEAM_SOURCES)),
        )
    ) or 0
    source_condition = (
        or_(Match.external_source.is_(None), Match.external_source.not_in(SYNTHETIC_TEAM_SOURCES))
        if real_match_count > 0
        else Match.external_source.in_(SYNTHETIC_TEAM_SOURCES)
    )
    matches = list(
        db.scalars(
            select(Match)
            .join(TeamA, Match.team_a_id == TeamA.id)
            .join(TeamB, Match.team_b_id == TeamB.id)
            .where(
                *base_conditions,
                source_condition,
            )
            .order_by(Match.start_time.asc().nullsfirst(), Match.id.asc())
        )
    )

    include_synthetic = real_match_count == 0
    id_to_identity = _identity_index_for_rating_scope(db, include_synthetic=include_synthetic)
    states: dict[int | str, EloState] = {}
    glicko_states: dict[int | str, GlickoState] = {}
    total_matches = len(matches)

    for index, match in enumerate(matches):
        identity_a = id_to_identity.get(match.team_a_id, match.team_a_id)
        identity_b = id_to_identity.get(match.team_b_id, match.team_b_id)
        if identity_a == identity_b:
            continue

        state_a = states.setdefault(identity_a, EloState())
        state_b = states.setdefault(identity_b, EloState())
        glicko_a = glicko_states.setdefault(identity_a, GlickoState())
        glicko_b = glicko_states.setdefault(identity_b, GlickoState())
        score_a = 1.0 if match.winner_team_id == match.team_a_id else 0.0
        match_weight = recency_multiplier(index, total_matches) * format_multiplier(match.format)
        new_a, new_b = update_elo(
            rating_a=state_a.rating,
            rating_b=state_b.rating,
            score_a=score_a,
            base_k=k_factor(match.tournament_tier),
            recency_weight=recency_multiplier(index, total_matches),
            series_weight=format_multiplier(match.format),
        )
        next_glicko_a, next_glicko_b = update_glicko_like(glicko_a, glicko_b, score_a, match_weight=match_weight)
        state_a.rating = new_a
        state_b.rating = new_b
        state_a.matches_count += 1
        state_b.matches_count += 1
        state_a.uncertainty = uncertainty(state_a.matches_count)
        state_b.uncertainty = uncertainty(state_b.matches_count)
        glicko_states[identity_a] = next_glicko_a
        glicko_states[identity_b] = next_glicko_b

    calculated_at = datetime.now(timezone.utc)
    db.execute(delete(TeamRating).where(TeamRating.rating_type.in_([RATING_TYPE, GLICKO_RATING_TYPE])))

    inserted = 0
    for team_id, identity in id_to_identity.items():
        state = states.get(identity)
        if state is None or state.matches_count == 0:
            continue
        db.add(
            TeamRating(
                team_id=team_id,
                rating_type=RATING_TYPE,
                rating_value=round(state.rating, 2),
                uncertainty=round(state.uncertainty, 2),
                matches_count=state.matches_count,
                calculated_at=calculated_at,
            )
        )
        inserted += 1
    for team_id, identity in id_to_identity.items():
        state = glicko_states.get(identity)
        if state is None or state.matches_count == 0:
            continue
        db.add(
            TeamRating(
                team_id=team_id,
                rating_type=GLICKO_RATING_TYPE,
                rating_value=round(state.rating, 2),
                uncertainty=round(state.rating_deviation, 2),
                matches_count=state.matches_count,
                calculated_at=calculated_at,
            )
        )
        inserted += 1

    db.commit()
    if total_matches == 0:
        return {"processed_matches": 0, "ratings_saved": 0, "dataset_scope": "none"}
    return {
        "processed_matches": total_matches,
        "ratings_saved": inserted,
        "dataset_scope": "real_only" if real_match_count > 0 else "synthetic_dev_only",
    }


def _identity_index_for_rating_scope(
    db: Session,
    *,
    include_synthetic: bool,
) -> dict[int, int | str]:
    id_to_identity, _ = build_team_identity_index(db)
    scoped: dict[int, int | str] = {}
    for team_id, source in db.execute(
        select(Team.id, Team.external_source).where(Team.is_active_tier1.is_(True))
    ):
        is_synthetic = source in SYNTHETIC_TEAM_SOURCES
        if is_synthetic != include_synthetic:
            continue
        scoped[team_id] = team_id if include_synthetic else id_to_identity.get(team_id, team_id)
    return scoped


def get_team_elo_rating(db: Session, team_id: int) -> TeamRating | None:
    return db.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id, TeamRating.rating_type == RATING_TYPE)
        .order_by(TeamRating.calculated_at.desc(), TeamRating.id.desc())
        .limit(1)
    )


def get_team_glicko_rating(db: Session, team_id: int) -> TeamRating | None:
    return db.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id, TeamRating.rating_type == GLICKO_RATING_TYPE)
        .order_by(TeamRating.calculated_at.desc(), TeamRating.id.desc())
        .limit(1)
    )


def get_team_rating_feature(db: Session, team_id: int) -> tuple[float, int]:
    rating = get_team_elo_rating(db, team_id)
    if rating is None:
        return rating_feature_value(STARTING_ELO), 0
    return rating_feature_value(rating.rating_value), rating.matches_count


def to_rating_read(rating: TeamRating) -> TeamRatingRead:
    return TeamRatingRead(
        team_id=str(rating.team_id),
        rating_type=rating.rating_type,
        rating_value=round(rating.rating_value),
        uncertainty=rating.uncertainty,
        matches_count=rating.matches_count,
        calculated_at=rating.calculated_at,
    )
