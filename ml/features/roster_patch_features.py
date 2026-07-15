from __future__ import annotations

from sqlalchemy import select

from app.db.models import Match
from app.patches.patch_service import calculate_days_since_patch, get_patch_for_match
from app.rosters.roster_service import (
    get_recent_standins_count,
    get_roster_stability_days,
    get_same_roster_matches_count,
    has_recent_roster_change,
)
from ml.features.rating_features import history_match_filter, team_identity_ids_for_history


def build_roster_patch_features(db, match: Match) -> dict:
    patch = get_patch_for_match(db, match.start_time)
    days_since_patch = calculate_days_since_patch(db, match.start_time)
    team_a_patch = _patch_record(db, match.team_a_id, match, patch)
    team_b_patch = _patch_record(db, match.team_b_id, match, patch)

    team_a_stability = get_roster_stability_days(db, match.team_a_id, match.start_time)
    team_b_stability = get_roster_stability_days(db, match.team_b_id, match.start_time)
    team_a_same_roster = get_same_roster_matches_count(db, match.team_a_id, match.start_time)
    team_b_same_roster = get_same_roster_matches_count(db, match.team_b_id, match.start_time)

    return {
        "team_a_roster_stability_days": team_a_stability,
        "team_b_roster_stability_days": team_b_stability,
        "roster_stability_diff": team_a_stability - team_b_stability,
        "team_a_same_roster_matches": team_a_same_roster,
        "team_b_same_roster_matches": team_b_same_roster,
        "same_roster_matches_diff": team_a_same_roster - team_b_same_roster,
        "team_a_recent_roster_change": has_recent_roster_change(db, match.team_a_id, match.start_time),
        "team_b_recent_roster_change": has_recent_roster_change(db, match.team_b_id, match.start_time),
        "team_a_recent_standins_count": get_recent_standins_count(db, match.team_a_id, match.start_time),
        "team_b_recent_standins_count": get_recent_standins_count(db, match.team_b_id, match.start_time),
        "current_patch": patch.patch_version if patch else None,
        "days_since_patch": days_since_patch,
        "is_current_patch": bool(patch and patch.is_current),
        "team_a_current_patch_winrate": team_a_patch["winrate"],
        "team_b_current_patch_winrate": team_b_patch["winrate"],
        "current_patch_winrate_diff": _diff(team_a_patch["winrate"], team_b_patch["winrate"]),
        "team_a_matches_current_patch": team_a_patch["matches"],
        "team_b_matches_current_patch": team_b_patch["matches"],
        "patch_recency_weight": _patch_recency_weight(days_since_patch),
    }


def _patch_record(db, team_id: int, current_match: Match, patch) -> dict:
    if patch is None or current_match.start_time is None:
        return {"matches": 0, "winrate": None}
    identity_ids = team_identity_ids_for_history(db, team_id)
    include_synthetic = current_match.external_source in {"dev_seed", "demo"}
    matches = list(
        db.scalars(
            select(Match).where(
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                history_match_filter(include_synthetic=include_synthetic),
                Match.start_time.is_not(None),
                Match.start_time < current_match.start_time,
                Match.start_time >= patch.release_date,
                ((Match.team_a_id.in_(identity_ids)) | (Match.team_b_id.in_(identity_ids))),
            )
        )
    )
    if not matches:
        return {"matches": 0, "winrate": None}
    wins = sum(1 for item in matches if item.winner_team_id in identity_ids)
    return {"matches": len(matches), "winrate": round(wins / len(matches), 4)}


def _diff(team_a_value: float | None, team_b_value: float | None) -> float | None:
    if team_a_value is None or team_b_value is None:
        return None
    return round(team_a_value - team_b_value, 4)


def _patch_recency_weight(days_since_patch: int | None) -> float:
    if days_since_patch is None:
        return 1.0
    return round(max(0.2, min(1.0, 1.0 - days_since_patch / 180)), 4)
