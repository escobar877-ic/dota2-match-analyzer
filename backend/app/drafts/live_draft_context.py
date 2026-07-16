from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.db.models import Match
from ml.config import ML_ARTIFACT_DIR


LIVE_MATCH_CONTEXT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "live_match_context_report.json"


def load_live_match_context(
    match_id: int,
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
    max_age_minutes: int = 45,
) -> dict[str, Any] | None:
    target = Path(path) if path is not None else LIVE_MATCH_CONTEXT_REPORT_PATH
    if not target.exists():
        return None
    try:
        report = json.loads(target.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(str(report["generated_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    generated_at = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=UTC)
    current_time = now or datetime.now(UTC)
    current_time = current_time if current_time.tzinfo else current_time.replace(tzinfo=UTC)
    if current_time - generated_at > timedelta(minutes=max_age_minutes):
        return None
    matches = report.get("matches")
    if not isinstance(matches, dict):
        return None
    context = matches.get(str(match_id))
    return context if isinstance(context, dict) and context.get("draft_available") else None


def live_context_to_draft_response(match: Match, context: dict[str, Any]) -> dict[str, Any]:
    team_a = context.get("team_a") if isinstance(context.get("team_a"), dict) else {}
    team_b = context.get("team_b") if isinstance(context.get("team_b"), dict) else {}
    team_a_picks = team_a.get("picks") if isinstance(team_a.get("picks"), list) else []
    team_b_picks = team_b.get("picks") if isinstance(team_b.get("picks"), list) else []
    entries: list[dict[str, Any]] = []
    for team_id, side, picks in (
        (match.team_a_id, str(team_a.get("side") or "unknown"), team_a_picks),
        (match.team_b_id, str(team_b.get("side") or "unknown"), team_b_picks),
    ):
        for index, pick in enumerate(picks, start=1):
            if not isinstance(pick, dict):
                continue
            hero_id = int(pick.get("hero_id") or 0)
            if hero_id <= 0:
                continue
            entries.append(
                {
                    "id": -len(entries) - 1,
                    "match_id": match.id,
                    "team_id": team_id,
                    "hero_id": hero_id,
                    "hero": {
                        "id": 0,
                        "hero_id": hero_id,
                        "localized_name": str(pick.get("localized_name") or f"Hero {hero_id}"),
                        "name": str(pick.get("hero_name") or f"hero_{hero_id}"),
                    },
                    "player_id": None,
                    "player_name": pick.get("player_name"),
                    "action_type": "pick",
                    "pick_order": index,
                    "ban_order": None,
                    "draft_order": len(entries) + 1,
                    "side": side,
                    "source": "opendota_live",
                }
            )
    return {
        "match_id": match.id,
        "draft_available": bool(entries),
        "draft_complete": len(team_a_picks) >= 5 and len(team_b_picks) >= 5,
        "team_a_picks_count": len(team_a_picks),
        "team_b_picks_count": len(team_b_picks),
        "team_a_bans_count": 0,
        "team_b_bans_count": 0,
        "entries": entries,
        "live_context": {
            "source": "opendota_live",
            "dota_match_id": context.get("dota_match_id"),
            "series_id": context.get("series_id"),
            "game_time_seconds": context.get("game_time_seconds"),
            "updated_at": context.get("updated_at"),
            "bans_available": False,
            "pick_order_available": False,
            "source_note": context.get("source_note"),
            "team_a": {
                "name": team_a.get("name"),
                "side": team_a.get("side"),
                "score": team_a.get("score"),
            },
            "team_b": {
                "name": team_b.get("name"),
                "side": team_b.get("side"),
                "score": team_b.get("score"),
            },
        },
    }
