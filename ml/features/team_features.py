from datetime import timedelta

from app.db.models import Match
from ml.features.rating_features import (
    build_elo_snapshot_before,
    get_team_historical_matches,
    team_identity_ids_for_history,
)


STRONG_TEAM_ELO = 1525
WEAKER_TEAM_ELO = 1475


def build_team_features(db, match: Match) -> dict:
    include_synthetic = match.external_source in {"dev_seed", "demo"}
    ratings = build_elo_snapshot_before(db, match.start_time, include_synthetic=include_synthetic)
    team_a_matches = get_team_historical_matches(db, match.team_a_id, match.start_time, limit=20)
    team_b_matches = get_team_historical_matches(db, match.team_b_id, match.start_time, limit=20)
    team_a_ids = team_identity_ids_for_history(db, match.team_a_id)
    team_b_ids = team_identity_ids_for_history(db, match.team_b_id)

    a_avg_opp = _avg_opponent_elo(team_a_matches[:10], team_a_ids, ratings)
    b_avg_opp = _avg_opponent_elo(team_b_matches[:10], team_b_ids, ratings)
    a_strong_wins = _wins_vs_strong(team_a_matches[:20], team_a_ids, ratings)
    b_strong_wins = _wins_vs_strong(team_b_matches[:20], team_b_ids, ratings)
    a_weak_losses = _losses_vs_weaker(team_a_matches[:20], team_a_ids, ratings)
    b_weak_losses = _losses_vs_weaker(team_b_matches[:20], team_b_ids, ratings)

    return {
        "team_a_avg_opponent_elo_last_10": a_avg_opp,
        "team_b_avg_opponent_elo_last_10": b_avg_opp,
        "opponent_elo_diff_last_10": round(a_avg_opp - b_avg_opp, 2) if a_avg_opp is not None and b_avg_opp is not None else None,
        "team_a_wins_vs_strong_teams_last_20": a_strong_wins,
        "team_b_wins_vs_strong_teams_last_20": b_strong_wins,
        "strong_team_wins_diff": a_strong_wins - b_strong_wins,
        "team_a_losses_vs_weaker_teams_last_20": a_weak_losses,
        "team_b_losses_vs_weaker_teams_last_20": b_weak_losses,
        "weak_loss_diff": a_weak_losses - b_weak_losses,
        "team_a_matches_count_last_30_days": _matches_last_30_days(team_a_matches, match),
        "team_b_matches_count_last_30_days": _matches_last_30_days(team_b_matches, match),
        "team_a_result_variance": _result_variance(team_a_matches[:20], team_a_ids),
        "team_b_result_variance": _result_variance(team_b_matches[:20], team_b_ids),
    }


def _avg_opponent_elo(matches: list[Match], team_ids: set[int], ratings: dict[int, float]) -> float | None:
    values = []
    for item in matches:
        opponent_id = item.team_b_id if item.team_a_id in team_ids else item.team_a_id
        if opponent_id in ratings:
            values.append(ratings[opponent_id])
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _wins_vs_strong(matches: list[Match], team_ids: set[int], ratings: dict[int, float]) -> int:
    wins = 0
    for item in matches:
        opponent_id = item.team_b_id if item.team_a_id in team_ids else item.team_a_id
        if ratings.get(opponent_id, 0) >= STRONG_TEAM_ELO and item.winner_team_id in team_ids:
            wins += 1
    return wins


def _losses_vs_weaker(matches: list[Match], team_ids: set[int], ratings: dict[int, float]) -> int:
    losses = 0
    for item in matches:
        opponent_id = item.team_b_id if item.team_a_id in team_ids else item.team_a_id
        if ratings.get(opponent_id, STRONG_TEAM_ELO) <= WEAKER_TEAM_ELO and item.winner_team_id not in team_ids:
            losses += 1
    return losses


def _matches_last_30_days(matches: list[Match], current_match: Match) -> int:
    if current_match.start_time is None:
        return 0
    cutoff = current_match.start_time - timedelta(days=30)
    return sum(1 for item in matches if item.start_time is not None and item.start_time >= cutoff)


def _result_variance(matches: list[Match], team_ids: set[int]) -> float | None:
    if len(matches) < 2:
        return None
    results = [1 if item.winner_team_id in team_ids else 0 for item in matches]
    mean = sum(results) / len(results)
    return round(sum((value - mean) ** 2 for value in results) / len(results), 4)
