from app.db.models import Match
from ml.features.rating_features import get_team_historical_matches, team_identity_ids_for_history


def build_recent_form_features(db, match: Match) -> dict:
    team_a_matches = get_team_historical_matches(db, match.team_a_id, match.start_time, limit=20)
    team_b_matches = get_team_historical_matches(db, match.team_b_id, match.start_time, limit=20)
    team_a_ids = team_identity_ids_for_history(db, match.team_a_id)
    team_b_ids = team_identity_ids_for_history(db, match.team_b_id)

    features = {}
    for window in [5, 10, 20]:
        a_rate = _win_rate(team_a_matches[:window], team_a_ids)
        b_rate = _win_rate(team_b_matches[:window], team_b_ids)
        features[f"team_a_winrate_last_{window}"] = a_rate
        features[f"team_b_winrate_last_{window}"] = b_rate
        features[f"form_diff_{window}"] = round(a_rate - b_rate, 4) if a_rate is not None and b_rate is not None else None
    a_weighted = recency_weighted_winrate(team_a_matches, team_a_ids)
    b_weighted = recency_weighted_winrate(team_b_matches, team_b_ids)
    a_momentum = recent_momentum(team_a_matches, team_a_ids)
    b_momentum = recent_momentum(team_b_matches, team_b_ids)
    features.update(
        {
            "team_a_recency_weighted_winrate": a_weighted,
            "team_b_recency_weighted_winrate": b_weighted,
            "recency_weighted_form_diff": round(a_weighted - b_weighted, 4)
            if a_weighted is not None and b_weighted is not None
            else None,
            "team_a_recent_momentum": a_momentum,
            "team_b_recent_momentum": b_momentum,
            "momentum_diff": round(a_momentum - b_momentum, 4) if a_momentum is not None and b_momentum is not None else None,
        }
    )
    return features


def _win_rate(matches: list[Match], team_ids: int | set[int]) -> float | None:
    if not matches:
        return None
    identities = {team_ids} if isinstance(team_ids, int) else team_ids
    wins = sum(1 for item in matches if item.winner_team_id in identities)
    return round(wins / len(matches), 4)


def recency_weighted_winrate(matches: list[Match], team_ids: int | set[int]) -> float | None:
    if not matches:
        return None
    weights = [1 / (index + 1) for index, _ in enumerate(matches)]
    identities = {team_ids} if isinstance(team_ids, int) else team_ids
    weighted_wins = sum(weight if item.winner_team_id in identities else 0 for weight, item in zip(weights, matches))
    return round(weighted_wins / sum(weights), 4)


def recent_momentum(matches: list[Match], team_ids: int | set[int]) -> float | None:
    if len(matches) < 6:
        return None
    last_3 = _win_rate(matches[:3], team_ids)
    previous_3 = _win_rate(matches[3:6], team_ids)
    if last_3 is None or previous_3 is None:
        return None
    return round(last_3 - previous_3, 4)
