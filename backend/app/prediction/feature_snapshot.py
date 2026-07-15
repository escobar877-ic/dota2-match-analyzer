from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, Team, TeamMatchStats
from app.prediction.fallback import (
    DEFAULT_HEAD_TO_HEAD,
    DEFAULT_HERO_POOL,
    DEFAULT_RECENT_FORM,
    DEFAULT_ROSTER_STABILITY,
    DEFAULT_TEAM_RATING,
)
from app.prediction.schemas import MatchFeatureSnapshot, TeamFeatureSnapshot
from app.ratings.rating_service import get_team_glicko_rating, get_team_rating_feature
from app.ratings.elo import STARTING_ELO, format_multiplier, k_factor, recency_multiplier, update_elo
from app.ratings.glicko import GlickoState, update_glicko_like
from app.ratings.team_identity import (
    SYNTHETIC_TEAM_SOURCES,
    build_team_identity_index,
    resolve_scoped_team_identity_ids,
)
from app.rosters.roster_service import get_active_roster


TIER1_HISTORY_SCOPE = "tier1"
VERIFIED_PRO_HISTORY_SCOPE = "verified_pro"


def build_match_feature_snapshot(
    db: Session,
    match: Match,
    *,
    history_scope: str = TIER1_HISTORY_SCOPE,
) -> MatchFeatureSnapshot:
    _validate_history_scope(history_scope)
    include_synthetic = match.external_source in SYNTHETIC_TEAM_SOURCES
    elo_ratings, glicko_ratings = _rating_snapshots_before(
        db,
        match.start_time,
        history_scope=history_scope,
        include_synthetic=include_synthetic,
    )
    team_a = build_team_feature_snapshot(
        db,
        match.team_a_id,
        cutoff=match.start_time,
        elo_rating=elo_ratings.get(match.team_a_id),
        glicko_rating=(
            glicko_ratings[match.team_a_id].rating
            if match.team_a_id in glicko_ratings
            else None
        ),
        rating_uncertainty=(
            glicko_ratings[match.team_a_id].rating_deviation
            if match.team_a_id in glicko_ratings
            else None
        ),
        history_scope=history_scope,
    )
    team_b = build_team_feature_snapshot(
        db,
        match.team_b_id,
        cutoff=match.start_time,
        elo_rating=elo_ratings.get(match.team_b_id),
        glicko_rating=(
            glicko_ratings[match.team_b_id].rating
            if match.team_b_id in glicko_ratings
            else None
        ),
        rating_uncertainty=(
            glicko_ratings[match.team_b_id].rating_deviation
            if match.team_b_id in glicko_ratings
            else None
        ),
        history_scope=history_scope,
    )
    h2h_value, h2h_count = calculate_head_to_head(
        db,
        match.team_a_id,
        match.team_b_id,
        cutoff=match.start_time,
        history_scope=history_scope,
    )

    return MatchFeatureSnapshot(
        match_id=match.id,
        team_a=team_a,
        team_b=team_b,
        head_to_head=h2h_value,
        head_to_head_count=h2h_count,
    )


def build_team_feature_snapshot(
    db: Session,
    team_id: int,
    *,
    cutoff: datetime | None = None,
    elo_rating: float | None = None,
    glicko_rating: float | None = None,
    rating_uncertainty: float | None = None,
    history_scope: str = TIER1_HISTORY_SCOPE,
) -> TeamFeatureSnapshot:
    _validate_history_scope(history_scope)
    identity_ids = _team_ids_for_scope(db, team_id, history_scope)
    include_synthetic = _team_is_synthetic(db, team_id)
    time_filter = Match.start_time < cutoff if cutoff is not None else True
    matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                _history_filter(history_scope, include_synthetic=include_synthetic),
                time_filter,
                or_(Match.team_a_id.in_(identity_ids), Match.team_b_id.in_(identity_ids)),
            )
            .order_by(Match.start_time.desc().nullslast(), Match.id.desc())
            .limit(20)
        )
    )

    recent_matches = matches[:10]
    recent_form = _recency_weighted_win_rate(recent_matches, identity_ids) if recent_matches else DEFAULT_RECENT_FORM
    if cutoff is None:
        elo_feature, elo_matches_count = get_team_rating_feature(db, team_id)
        current_glicko = get_team_glicko_rating(db, team_id)
        glicko_feature = _rating_feature_value(current_glicko.rating_value) if current_glicko else None
    else:
        elo_feature = _rating_feature_value(elo_rating)
        elo_matches_count = len(matches)
        glicko_feature = _rating_feature_value(glicko_rating)
    if elo_matches_count and glicko_feature is not None:
        overall_rating = elo_feature * 0.75 + glicko_feature * 0.25
    else:
        overall_rating = elo_feature if elo_matches_count else DEFAULT_TEAM_RATING

    stats_rows = list(
        db.scalars(
            select(TeamMatchStats)
            .join(Match, TeamMatchStats.match_id == Match.id)
            .where(TeamMatchStats.team_id.in_(identity_ids))
            .where(_history_filter(history_scope, include_synthetic=include_synthetic))
            .where(Match.start_time < cutoff if cutoff is not None else True)
            .order_by(Match.start_time.desc().nullslast(), TeamMatchStats.id.desc())
            .limit(20)
        )
    )
    hero_pool = _hero_pool_proxy(stats_rows)

    active_roster = get_active_roster(db, team_id, cutoff)
    roster_names = {
        entry.player.nickname.casefold()
        for entry in active_roster
        if entry.player and entry.player.nickname
    }
    roster_count = len(roster_names)
    roster_stability = _roster_completeness(roster_count) if roster_count else DEFAULT_ROSTER_STABILITY

    return TeamFeatureSnapshot(
        team_id=team_id,
        recent_form=recent_form,
        rating=overall_rating,
        hero_pool=hero_pool,
        roster_stability=roster_stability,
        matches_count=max(len(matches), elo_matches_count),
        roster_count=roster_count,
        stats_count=len(stats_rows),
        elo_rating=round(elo_rating, 2) if elo_rating is not None else None,
        glicko_rating=round(glicko_rating, 2) if glicko_rating is not None else None,
        rating_uncertainty=round(rating_uncertainty, 2) if rating_uncertainty is not None else None,
        history_scope=history_scope,
    )


def _roster_completeness(roster_count: int) -> float:
    if roster_count == 5:
        return 1.0
    if roster_count < 5:
        return max(0.0, roster_count / 5)
    # More than five active players means the source includes substitutes or an
    # unresolved roster transition, so it must not look fully stable.
    return 0.6


def calculate_head_to_head(
    db: Session,
    team_a_id: int,
    team_b_id: int,
    *,
    cutoff: datetime | None = None,
    history_scope: str = TIER1_HISTORY_SCOPE,
) -> tuple[float, int]:
    _validate_history_scope(history_scope)
    team_a_ids = _team_ids_for_scope(db, team_a_id, history_scope)
    team_b_ids = _team_ids_for_scope(db, team_b_id, history_scope)
    include_synthetic = _team_is_synthetic(db, team_a_id)
    matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                _history_filter(history_scope, include_synthetic=include_synthetic),
                Match.start_time < cutoff if cutoff is not None else True,
                or_(
                    and_(Match.team_a_id.in_(team_a_ids), Match.team_b_id.in_(team_b_ids)),
                    and_(Match.team_a_id.in_(team_b_ids), Match.team_b_id.in_(team_a_ids)),
                ),
            )
            .order_by(Match.start_time.desc().nullslast(), Match.id.desc())
            .limit(10)
        )
    )
    if not matches:
        return DEFAULT_HEAD_TO_HEAD, 0

    team_a_wins = sum(1 for match in matches if match.winner_team_id in team_a_ids)
    return (team_a_wins / len(matches)) - 0.5, len(matches)


def _rating_snapshots_before(
    db: Session,
    cutoff: datetime | None,
    *,
    history_scope: str = TIER1_HISTORY_SCOPE,
    include_synthetic: bool = False,
) -> tuple[dict[int, float], dict[int, GlickoState]]:
    if cutoff is None:
        return {}, {}
    matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                Match.start_time < cutoff,
                _history_filter(history_scope, include_synthetic=include_synthetic),
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
        )
    )
    id_to_identity = _identity_index_for_source_scope(db, include_synthetic)
    elo_by_identity: dict[int | str, float] = {}
    glicko_by_identity: dict[int | str, GlickoState] = {}
    total = len(matches)
    for index, historical_match in enumerate(matches):
        identity_a: int | str = id_to_identity.get(historical_match.team_a_id, historical_match.team_a_id)
        identity_b: int | str = id_to_identity.get(historical_match.team_b_id, historical_match.team_b_id)
        if identity_a == identity_b:
            continue
        elo_a = elo_by_identity.get(identity_a, STARTING_ELO)
        elo_b = elo_by_identity.get(identity_b, STARTING_ELO)
        glicko_a = glicko_by_identity.get(identity_a, GlickoState())
        glicko_b = glicko_by_identity.get(identity_b, GlickoState())
        score_a = 1.0 if historical_match.winner_team_id == historical_match.team_a_id else 0.0
        recency = recency_multiplier(index, total)
        series = format_multiplier(historical_match.format)
        elo_by_identity[identity_a], elo_by_identity[identity_b] = update_elo(
            rating_a=elo_a,
            rating_b=elo_b,
            score_a=score_a,
            base_k=k_factor(historical_match.tournament_tier),
            recency_weight=recency,
            series_weight=series,
        )
        glicko_by_identity[identity_a], glicko_by_identity[identity_b] = update_glicko_like(
            glicko_a,
            glicko_b,
            score_a,
            match_weight=recency * series,
        )
    elo = {
        team_id: elo_by_identity[identity]
        for team_id, identity in id_to_identity.items()
        if identity in elo_by_identity
    }
    glicko = {
        team_id: glicko_by_identity[identity]
        for team_id, identity in id_to_identity.items()
        if identity in glicko_by_identity
    }
    return elo, glicko


def _win_rate(matches: list[Match], team_ids: set[int]) -> float:
    if not matches:
        return 0.5
    wins = sum(1 for match in matches if match.winner_team_id in team_ids)
    return wins / len(matches)


def _recency_weighted_win_rate(matches: list[Match], team_ids: set[int]) -> float:
    if not matches:
        return 0.5
    weights = [1 / (index + 1) for index, _ in enumerate(matches)]
    weighted_wins = sum(weight if match.winner_team_id in team_ids else 0 for weight, match in zip(weights, matches))
    return weighted_wins / sum(weights)


def _rating_feature_value(rating: float | None) -> float:
    if rating is None:
        return 0.5
    return max(0.2, min(0.8, 0.5 + (rating - 1500.0) / 800))


def _hero_pool_proxy(stats_rows: list[TeamMatchStats]) -> float:
    if not stats_rows:
        return DEFAULT_HERO_POOL

    avg_assists = sum((row.assists or 0) for row in stats_rows) / len(stats_rows)
    avg_kills = sum((row.kills or 0) for row in stats_rows) / len(stats_rows)
    avg_deaths = sum((row.deaths or 0) for row in stats_rows) / len(stats_rows)
    activity_score = ((avg_kills + avg_assists * 0.45) - avg_deaths * 0.35) / 45
    return max(0.25, min(0.75, 0.5 + activity_score))


def _team_ids_for_scope(db: Session, team_id: int, history_scope: str) -> set[int]:
    return resolve_scoped_team_identity_ids(db, team_id)


def _history_filter(history_scope: str, *, include_synthetic: bool = False):
    if history_scope == VERIFIED_PRO_HISTORY_SCOPE:
        eligible = or_(
            Match.is_tier1_match.is_(True),
            and_(
                Match.competition_tier == "pro",
                Match.verification_status == "verified",
                Match.source_confidence.in_(["high", "medium"]),
            ),
        )
    else:
        eligible = Match.is_tier1_match.is_(True)
    source_filter = (
        Match.external_source.in_(SYNTHETIC_TEAM_SOURCES)
        if include_synthetic
        else or_(
            Match.external_source.is_(None),
            Match.external_source.notin_(SYNTHETIC_TEAM_SOURCES),
        )
    )
    return and_(eligible, source_filter)


def _team_is_synthetic(db: Session, team_id: int) -> bool:
    team = db.get(Team, team_id)
    return bool(team and team.external_source in SYNTHETIC_TEAM_SOURCES)


def _identity_index_for_source_scope(
    db: Session,
    include_synthetic: bool,
) -> dict[int, int | str]:
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


def _validate_history_scope(history_scope: str) -> None:
    if history_scope not in {TIER1_HISTORY_SCOPE, VERIFIED_PRO_HISTORY_SCOPE}:
        raise ValueError(f"Unknown history scope: {history_scope}")
