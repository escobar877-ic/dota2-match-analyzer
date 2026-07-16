from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload


backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from app.database import SessionLocal
from app.db.models import Match
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.normalizer import normalize_lookup_key
from worker.data_ingestion.opendota_client import OpenDotaClient


LIVE_MATCH_CONTEXT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "live_match_context_report.json"
LIVE_MATCH_WINDOW_SECONDS = 12 * 60 * 60


def sync_live_match_context(
    *,
    artifact_path: str | Path | None = LIVE_MATCH_CONTEXT_REPORT_PATH,
    db: Session | None = None,
    client: OpenDotaClient | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    db = db or SessionLocal()
    client = client or OpenDotaClient()
    generated_at = datetime.now(UTC)
    warnings: list[str] = []
    errors: list[str] = []
    contexts: dict[str, dict[str, Any]] = {}

    try:
        live_response = client.get_live_matches()
        if not live_response.ok:
            errors.append(live_response.error or "OpenDota live request failed.")
            report = _build_report(generated_at, contexts, 0, warnings, errors)
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
            return report

        live_records = live_response.data if isinstance(live_response.data, list) else []
        heroes_response = client.get_heroes()
        hero_map = _hero_map(heroes_response.data if heroes_response.ok else None)
        if not heroes_response.ok:
            warnings.append(heroes_response.error or "OpenDota hero constants request failed; hero IDs are shown without names.")

        matches = list(
            db.scalars(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(Match.status == "live")
            ).all()
        )
        for match in matches:
            live_record = _find_live_record(match, live_records)
            if live_record is None:
                continue
            context = _build_match_context(match, live_record, hero_map, generated_at)
            if context["draft_available"]:
                contexts[str(match.id)] = context

        report = _build_report(generated_at, contexts, len(live_records), warnings, errors)
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        if owns_session:
            db.close()


def _find_live_record(match: Match, records: list[Any]) -> dict[str, Any] | None:
    if not match.team_a or not match.team_b:
        return None
    expected_pair = {
        normalize_lookup_key(match.team_a.name),
        normalize_lookup_key(match.team_b.name),
    }
    candidates: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        raw_pair = {
            normalize_lookup_key(str(raw.get("team_name_radiant") or "")),
            normalize_lookup_key(str(raw.get("team_name_dire") or "")),
        }
        if "" in raw_pair or raw_pair != expected_pair:
            continue
        if not _start_time_is_plausible(match.start_time, raw.get("activate_time")):
            continue
        candidates.append(raw)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("activate_time") or 0))


def _start_time_is_plausible(match_start: datetime | None, activate_time: Any) -> bool:
    if match_start is None or not activate_time:
        return True
    normalized_start = match_start if match_start.tzinfo else match_start.replace(tzinfo=UTC)
    try:
        live_start = datetime.fromtimestamp(int(activate_time), tz=UTC)
    except (TypeError, ValueError, OSError):
        return True
    return abs((live_start - normalized_start).total_seconds()) <= LIVE_MATCH_WINDOW_SECONDS


def _build_match_context(
    match: Match,
    raw: dict[str, Any],
    hero_map: dict[int, dict[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    radiant_name = str(raw.get("team_name_radiant") or "")
    dire_name = str(raw.get("team_name_dire") or "")
    team_a_side = "radiant" if normalize_lookup_key(match.team_a.name) == normalize_lookup_key(radiant_name) else "dire"
    team_b_side = "dire" if team_a_side == "radiant" else "radiant"
    players = raw.get("players") if isinstance(raw.get("players"), list) else []
    team_a_picks = _picks_for_side(players, team_a_side, hero_map)
    team_b_picks = _picks_for_side(players, team_b_side, hero_map)
    return {
        "database_match_id": match.id,
        "dota_match_id": str(raw.get("match_id") or ""),
        "series_id": str(raw.get("series_id") or "") or None,
        "league_id": raw.get("league_id"),
        "updated_at": generated_at.isoformat(),
        "game_time_seconds": raw.get("game_time"),
        "draft_available": bool(team_a_picks or team_b_picks),
        "draft_complete": len(team_a_picks) >= 5 and len(team_b_picks) >= 5,
        "bans_available": False,
        "pick_order_available": False,
        "source": "opendota_live",
        "source_note": "OpenDota live feed exposes current hero picks but not bans or original draft order.",
        "team_a": {
            "id": match.team_a_id,
            "name": match.team_a.name,
            "side": team_a_side,
            "score": raw.get("radiant_score") if team_a_side == "radiant" else raw.get("dire_score"),
            "picks": team_a_picks,
        },
        "team_b": {
            "id": match.team_b_id,
            "name": match.team_b.name,
            "side": team_b_side,
            "score": raw.get("dire_score") if team_b_side == "dire" else raw.get("radiant_score"),
            "picks": team_b_picks,
        },
    }


def _picks_for_side(players: list[Any], side: str, hero_map: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    expected_team = 0 if side == "radiant" else 1
    picks: list[dict[str, Any]] = []
    for player in players:
        if not isinstance(player, dict) or player.get("team") != expected_team:
            continue
        try:
            hero_id = int(player.get("hero_id") or 0)
        except (TypeError, ValueError):
            continue
        if hero_id <= 0:
            continue
        hero = hero_map.get(hero_id) or {}
        picks.append(
            {
                "hero_id": hero_id,
                "hero_name": str(hero.get("name") or f"hero_{hero_id}"),
                "localized_name": str(hero.get("localized_name") or f"Hero {hero_id}"),
                "player_name": player.get("name"),
                "account_id": player.get("account_id"),
                "team_slot": player.get("team_slot"),
            }
        )
    return sorted(picks, key=lambda item: (int(item.get("team_slot") or 99), item["hero_id"]))


def _hero_map(payload: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        try:
            hero_id = int(value.get("id"))
        except (TypeError, ValueError):
            continue
        result[hero_id] = value
    return result


def _build_report(
    generated_at: datetime,
    contexts: dict[str, dict[str, Any]],
    live_records_seen: int,
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "status": "failed" if errors else "warning" if warnings else "ok",
        "generated_at": generated_at.isoformat(),
        "source": "opendota_live",
        "live_records_seen": live_records_seen,
        "matched_live_matches": len(contexts),
        "drafts_available": sum(1 for context in contexts.values() if context.get("draft_available")),
        "training_changed": False,
        "prediction_changed": False,
        "warnings": warnings,
        "errors": errors,
        "matches": contexts,
    }


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh read-only live Dota picks from OpenDota.")
    parser.parse_args()
    sync_live_match_context()


if __name__ == "__main__":
    main()
