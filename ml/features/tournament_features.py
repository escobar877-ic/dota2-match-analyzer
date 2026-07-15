from app.db.models import Match
from ml.features.rating_features import get_team_historical_matches, team_identity_ids_for_history


def build_tournament_features(match: Match, db=None) -> dict:
    tournament_name = (match.tournament_name or "").lower()
    features = {
        "tournament_tier": match.tournament_tier,
        "match_format": match.format,
        "is_playoff": "playoff" in tournament_name or "final" in tournament_name,
        "is_elimination_match": "elimination" in tournament_name or "lower bracket" in tournament_name,
    }
    if db is None:
        features.update(_empty_context())
        return features

    team_a_matches = get_team_historical_matches(db, match.team_a_id, match.start_time, limit=40)
    team_b_matches = get_team_historical_matches(db, match.team_b_id, match.start_time, limit=40)
    team_a_ids = team_identity_ids_for_history(db, match.team_a_id)
    team_b_ids = team_identity_ids_for_history(db, match.team_b_id)
    a_tournament = _winrate(
        [item for item in team_a_matches if _same_tournament(item.tournament_name, match.tournament_name)][:10],
        team_a_ids,
    )
    b_tournament = _winrate(
        [item for item in team_b_matches if _same_tournament(item.tournament_name, match.tournament_name)][:10],
        team_b_ids,
    )
    a_bo3 = _winrate([item for item in team_a_matches if _format(item) == "bo3"], team_a_ids)
    b_bo3 = _winrate([item for item in team_b_matches if _format(item) == "bo3"], team_b_ids)
    a_bo5 = _winrate([item for item in team_a_matches if _format(item) == "bo5"], team_a_ids)
    b_bo5 = _winrate([item for item in team_b_matches if _format(item) == "bo5"], team_b_ids)
    features.update(
        {
            "team_a_tournament_recent_winrate": a_tournament,
            "team_b_tournament_recent_winrate": b_tournament,
            "tournament_recent_winrate_diff": _diff(a_tournament, b_tournament),
            "team_a_bo3_winrate": a_bo3,
            "team_b_bo3_winrate": b_bo3,
            "bo3_winrate_diff": _diff(a_bo3, b_bo3),
            "team_a_bo5_winrate": a_bo5,
            "team_b_bo5_winrate": b_bo5,
            "bo5_winrate_diff": _diff(a_bo5, b_bo5),
        }
    )
    return features


def _empty_context() -> dict:
    return {
        "team_a_tournament_recent_winrate": None,
        "team_b_tournament_recent_winrate": None,
        "tournament_recent_winrate_diff": None,
        "team_a_bo3_winrate": None,
        "team_b_bo3_winrate": None,
        "bo3_winrate_diff": None,
        "team_a_bo5_winrate": None,
        "team_b_bo5_winrate": None,
        "bo5_winrate_diff": None,
    }


def _same_tournament(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip().lower() == right.strip().lower())


def _format(match: Match) -> str:
    return (match.format or "").strip().lower()


def _winrate(matches: list[Match], team_ids: int | set[int]) -> float | None:
    if not matches:
        return None
    identities = {team_ids} if isinstance(team_ids, int) else team_ids
    return round(sum(1 for item in matches if item.winner_team_id in identities) / len(matches), 4)


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)
