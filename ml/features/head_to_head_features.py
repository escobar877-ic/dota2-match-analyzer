from sqlalchemy import and_, or_, select

from app.db.models import Match
from ml.features.rating_features import history_match_filter, team_identity_ids_for_history


def build_head_to_head_features(db, match: Match) -> dict:
    if match.start_time is None:
        return {
            "h2h_matches_count": 0,
            "h2h_team_a_winrate": None,
            "h2h_recent_weighted_score": None,
        }

    team_a_ids = team_identity_ids_for_history(db, match.team_a_id)
    team_b_ids = team_identity_ids_for_history(db, match.team_b_id)
    include_synthetic = match.external_source in {"dev_seed", "demo"}
    h2h_matches = list(
        db.scalars(
            select(Match)
            .where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                Match.start_time < match.start_time,
                history_match_filter(include_synthetic=include_synthetic),
                or_(
                    and_(Match.team_a_id.in_(team_a_ids), Match.team_b_id.in_(team_b_ids)),
                    and_(Match.team_a_id.in_(team_b_ids), Match.team_b_id.in_(team_a_ids)),
                ),
            )
            .order_by(Match.start_time.desc(), Match.id.desc())
            .limit(10)
        )
    )
    if not h2h_matches:
        return {
            "h2h_matches_count": 0,
            "h2h_team_a_winrate": None,
            "h2h_recent_weighted_score": None,
        }

    wins = [1 if item.winner_team_id in team_a_ids else 0 for item in h2h_matches]
    weighted_score = _weighted_average(wins)
    return {
        "h2h_matches_count": len(h2h_matches),
        "h2h_team_a_winrate": round(sum(wins) / len(wins), 4),
        "h2h_recent_weighted_score": round(weighted_score, 4),
    }


def _weighted_average(values: list[int]) -> float:
    weights = [1 / (index + 1) for index in range(len(values))]
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)
