from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, Team
from app.ratings.elo import STARTING_ELO, format_multiplier, k_factor, recency_multiplier, update_elo
from app.ratings.glicko import GlickoState, update_glicko_like
from app.ratings.team_identity import (
    SYNTHETIC_TEAM_SOURCES,
    build_team_identity_index,
    resolve_scoped_team_identity_ids,
)


def build_rating_features(db: Session, match: Match) -> dict:
    include_synthetic = match.external_source in SYNTHETIC_TEAM_SOURCES
    ratings = build_elo_snapshot_before(db, match.start_time, include_synthetic=include_synthetic)
    glicko = build_glicko_snapshot_before(db, match.start_time, include_synthetic=include_synthetic)
    team_a_elo = ratings.get(match.team_a_id)
    team_b_elo = ratings.get(match.team_b_id)
    team_a_glicko = glicko.get(match.team_a_id)
    team_b_glicko = glicko.get(match.team_b_id)

    return {
        "team_a_elo": round(team_a_elo) if team_a_elo is not None else None,
        "team_b_elo": round(team_b_elo) if team_b_elo is not None else None,
        "elo_diff": round(team_a_elo - team_b_elo, 2) if team_a_elo is not None and team_b_elo is not None else None,
        "team_a_glicko": round(team_a_glicko.rating, 2) if team_a_glicko is not None else None,
        "team_b_glicko": round(team_b_glicko.rating, 2) if team_b_glicko is not None else None,
        "glicko_diff": round(team_a_glicko.rating - team_b_glicko.rating, 2)
        if team_a_glicko is not None and team_b_glicko is not None
        else None,
        "team_a_rating_uncertainty": round(team_a_glicko.rating_deviation, 2) if team_a_glicko is not None else None,
        "team_b_rating_uncertainty": round(team_b_glicko.rating_deviation, 2) if team_b_glicko is not None else None,
        "rating_uncertainty_diff": round(team_a_glicko.rating_deviation - team_b_glicko.rating_deviation, 2)
        if team_a_glicko is not None and team_b_glicko is not None
        else None,
    }


def build_elo_snapshot_before(
    db: Session,
    cutoff: datetime | None,
    *,
    include_synthetic: bool = False,
) -> dict[int, float]:
    if cutoff is None:
        return {}

    matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                Match.start_time < cutoff,
                history_match_filter(include_synthetic=include_synthetic),
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
        )
    )
    total_matches = len(matches)
    id_to_identity = _identity_index_for_scope(db, include_synthetic)
    ratings: dict[int | str, float] = {}

    for index, historical_match in enumerate(matches):
        identity_a = id_to_identity.get(historical_match.team_a_id, historical_match.team_a_id)
        identity_b = id_to_identity.get(historical_match.team_b_id, historical_match.team_b_id)
        if identity_a == identity_b:
            continue
        rating_a = ratings.get(identity_a, STARTING_ELO)
        rating_b = ratings.get(identity_b, STARTING_ELO)
        score_a = 1.0 if historical_match.winner_team_id == historical_match.team_a_id else 0.0
        new_a, new_b = update_elo(
            rating_a=rating_a,
            rating_b=rating_b,
            score_a=score_a,
            base_k=k_factor(historical_match.tournament_tier),
            recency_weight=recency_multiplier(index, total_matches),
            series_weight=format_multiplier(historical_match.format) * _history_weight(historical_match),
        )
        ratings[identity_a] = new_a
        ratings[identity_b] = new_b

    return _expand_identity_values(id_to_identity, ratings)


def build_glicko_snapshot_before(
    db: Session,
    cutoff: datetime | None,
    *,
    include_synthetic: bool = False,
) -> dict[int, GlickoState]:
    if cutoff is None:
        return {}

    matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                Match.start_time < cutoff,
                history_match_filter(include_synthetic=include_synthetic),
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
        )
    )
    total_matches = len(matches)
    id_to_identity = _identity_index_for_scope(db, include_synthetic)
    ratings: dict[int | str, GlickoState] = {}

    for index, historical_match in enumerate(matches):
        identity_a = id_to_identity.get(historical_match.team_a_id, historical_match.team_a_id)
        identity_b = id_to_identity.get(historical_match.team_b_id, historical_match.team_b_id)
        if identity_a == identity_b:
            continue
        state_a = ratings.get(identity_a, GlickoState())
        state_b = ratings.get(identity_b, GlickoState())
        score_a = 1.0 if historical_match.winner_team_id == historical_match.team_a_id else 0.0
        match_weight = (
            recency_multiplier(index, total_matches)
            * format_multiplier(historical_match.format)
            * _history_weight(historical_match)
        )
        ratings[identity_a], ratings[identity_b] = update_glicko_like(
            state_a,
            state_b,
            score_a,
            match_weight=match_weight,
        )

    return _expand_identity_values(id_to_identity, ratings)


def get_team_historical_matches(db: Session, team_id: int, cutoff: datetime | None, limit: int | None = None) -> list[Match]:
    if cutoff is None:
        return []

    team = db.get(Team, team_id)
    include_synthetic = bool(team and team.external_source in SYNTHETIC_TEAM_SOURCES)
    identity_ids = resolve_scoped_team_identity_ids(db, team_id)
    statement = (
        select(Match)
        .where(
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.start_time.is_not(None),
            Match.start_time < cutoff,
            history_match_filter(include_synthetic=include_synthetic),
            or_(Match.team_a_id.in_(identity_ids), Match.team_b_id.in_(identity_ids)),
        )
        .order_by(Match.start_time.desc(), Match.id.desc())
    )
    if limit:
        statement = statement.limit(limit)
    return list(db.scalars(statement))


def team_identity_ids_for_history(db: Session, team_id: int) -> set[int]:
    return resolve_scoped_team_identity_ids(db, team_id)


def history_match_filter(*, include_synthetic: bool = False):
    eligible = or_(
        Match.is_tier1_match.is_(True),
        and_(
            Match.competition_tier == "pro",
            Match.verification_status == "verified",
            Match.is_training_eligible.is_(True),
        ),
    )
    if include_synthetic:
        return and_(eligible, Match.external_source.in_(SYNTHETIC_TEAM_SOURCES))
    return and_(
        eligible,
        or_(
            Match.external_source.is_(None),
            Match.external_source.notin_(SYNTHETIC_TEAM_SOURCES),
        ),
    )


def _history_weight(match: Match) -> float:
    return 0.5 if match.competition_tier == "pro" else 1.0


def _identity_index_for_scope(db: Session, include_synthetic: bool) -> dict[int, int | str]:
    if include_synthetic:
        return {
            team_id: team_id
            for team_id, source in db.execute(select(Team.id, Team.external_source))
            if source in SYNTHETIC_TEAM_SOURCES
        }
    id_to_identity, _ = build_team_identity_index(db)
    real_ids = {
        team_id
        for team_id, source in db.execute(select(Team.id, Team.external_source))
        if source not in SYNTHETIC_TEAM_SOURCES
    }
    return {team_id: identity for team_id, identity in id_to_identity.items() if team_id in real_ids}


def _expand_identity_values(id_to_identity: dict[int, int | str], values: dict[int | str, object]) -> dict:
    return {
        team_id: values[identity]
        for team_id, identity in id_to_identity.items()
        if identity in values
    }
