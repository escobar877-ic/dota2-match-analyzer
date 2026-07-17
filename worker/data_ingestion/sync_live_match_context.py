from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
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
from worker.data_ingestion.normalizer import normalize_lookup_key, normalize_team_name
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.source_mapping import load_source_mappings, validate_source_mapping


LIVE_MATCH_CONTEXT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "live_match_context_report.json"
LIVE_MATCH_WINDOW_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class LiveRecordMatch:
    raw: dict[str, Any]
    team_a_side: str
    identity_method: str


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
    availability: dict[str, dict[str, Any]] = {}
    roster_cache: dict[str, set[int] | None] = {}
    identity_fallback_attempts = 0
    identity_fallback_matches = 0
    anonymous_live_records = 0

    try:
        live_response = client.get_live_matches()
        if not live_response.ok:
            errors.append(live_response.error or "OpenDota live request failed.")
            report = _build_report(generated_at, contexts, availability, 0, 0, 0, 0, warnings, errors)
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
            return report

        live_records = live_response.data if isinstance(live_response.data, list) else []
        anonymous_live_records = sum(
            1
            for raw in live_records
            if isinstance(raw, dict)
            and not str(raw.get("team_name_radiant") or "").strip()
            and not str(raw.get("team_name_dire") or "").strip()
        )
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
        try:
            mappings = load_source_mappings()
            mapping_status = validate_source_mapping()
            mappings_valid = mapping_status.get("status") == "ok"
            if not mappings_valid:
                warnings.append("Source mappings are invalid; live roster identity fallback is disabled.")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            mappings = {}
            mappings_valid = False
            warnings.append(f"Source mappings could not be loaded; live roster identity fallback is disabled: {exc}")

        for match in matches:
            matched = _find_named_live_record(match, live_records)
            fallback_reason = None
            if matched is None and mappings_valid:
                identity_fallback_attempts += 1
                team_a_accounts, team_a_reason = _verified_team_account_ids(
                    client,
                    match.team_a.name,
                    mappings,
                    roster_cache,
                )
                team_b_accounts, team_b_reason = _verified_team_account_ids(
                    client,
                    match.team_b.name,
                    mappings,
                    roster_cache,
                )
                if team_a_accounts is not None and team_b_accounts is not None:
                    matched = _find_roster_live_record(match, live_records, team_a_accounts, team_b_accounts)
                    if matched is not None:
                        identity_fallback_matches += 1
                    else:
                        fallback_reason = "no_exact_5v5_account_identity_match"
                else:
                    fallback_reason = team_a_reason or team_b_reason or "verified_roster_identity_unavailable"
            elif matched is None:
                fallback_reason = "verified_source_mappings_unavailable"

            if matched is None:
                availability[str(match.id)] = _unavailable_context(match, fallback_reason)
                continue
            context = _build_match_context(match, matched, hero_map, generated_at)
            availability[str(match.id)] = {
                "status": "matched",
                "reason": None,
                "message": "Live picks matched to this series using verified source identity.",
                "identity_method": matched.identity_method,
            }
            if context["draft_available"]:
                contexts[str(match.id)] = context

        report = _build_report(
            generated_at,
            contexts,
            availability,
            len(live_records),
            anonymous_live_records,
            identity_fallback_attempts,
            identity_fallback_matches,
            warnings,
            errors,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        if owns_session:
            db.close()


def _find_named_live_record(match: Match, records: list[Any]) -> LiveRecordMatch | None:
    if not match.team_a or not match.team_b:
        return None
    expected_pair = {
        _team_identity(match.team_a.name),
        _team_identity(match.team_b.name),
    }
    candidates: list[LiveRecordMatch] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        raw_pair = {
            _team_identity(str(raw.get("team_name_radiant") or "")),
            _team_identity(str(raw.get("team_name_dire") or "")),
        }
        if "" in raw_pair or raw_pair != expected_pair:
            continue
        if not _start_time_is_plausible(match.start_time, raw.get("activate_time")):
            continue
        team_a_side = (
            "radiant"
            if _team_identity(match.team_a.name) == _team_identity(str(raw.get("team_name_radiant") or ""))
            else "dire"
        )
        candidates.append(LiveRecordMatch(raw=raw, team_a_side=team_a_side, identity_method="team_names"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.raw.get("activate_time") or 0))


def _find_roster_live_record(
    match: Match,
    records: list[Any],
    team_a_accounts: set[int],
    team_b_accounts: set[int],
) -> LiveRecordMatch | None:
    candidates: list[LiveRecordMatch] = []
    for raw in records:
        if not isinstance(raw, dict) or not _start_time_is_plausible(match.start_time, raw.get("activate_time")):
            continue
        radiant, dire = _live_side_account_ids(raw)
        if radiant == team_a_accounts and dire == team_b_accounts:
            candidates.append(LiveRecordMatch(raw=raw, team_a_side="radiant", identity_method="verified_5v5_account_ids"))
        elif radiant == team_b_accounts and dire == team_a_accounts:
            candidates.append(LiveRecordMatch(raw=raw, team_a_side="dire", identity_method="verified_5v5_account_ids"))
    active_candidates = [candidate for candidate in candidates if not _positive_int(candidate.raw.get("deactivate_time"))]
    if len(active_candidates) == 1:
        return active_candidates[0]
    if active_candidates or len(candidates) != 1:
        return None
    return candidates[0]


def _verified_team_account_ids(
    client: OpenDotaClient,
    canonical_name: str,
    mappings: dict[str, Any],
    cache: dict[str, set[int] | None],
) -> tuple[set[int] | None, str | None]:
    identity = _team_identity(canonical_name)
    if identity in cache:
        cached = cache[identity]
        return cached, None if cached is not None else "verified_roster_identity_unavailable"
    team_id = _mapped_open_dota_team_id(canonical_name, mappings)
    if team_id is None:
        cache[identity] = None
        return None, "opendota_team_mapping_missing"
    response = client.get_team_players(team_id)
    if not response.ok or not isinstance(response.data, list):
        cache[identity] = None
        return None, "opendota_current_roster_fetch_failed"
    account_ids = {
        _positive_int(item.get("account_id"))
        for item in response.data
        if isinstance(item, dict) and item.get("is_current_team_member") is True
    }
    account_ids.discard(0)
    if len(account_ids) != 5:
        cache[identity] = None
        return None, "opendota_current_roster_not_exactly_five"
    cache[identity] = account_ids
    return account_ids, None


def _mapped_open_dota_team_id(canonical_name: str, mappings: dict[str, Any]) -> str | None:
    teams = mappings.get("opendota", {}).get("teams", {}) if isinstance(mappings.get("opendota"), dict) else {}
    if not isinstance(teams, dict):
        return None
    expected = _team_identity(canonical_name)
    candidates = []
    for external_id, value in teams.items():
        canonical = value.get("canonical_name") if isinstance(value, dict) else value
        if _team_identity(str(canonical or "")) == expected and str(external_id).isdigit():
            candidates.append(str(external_id))
    return candidates[0] if len(candidates) == 1 else None


def _live_side_account_ids(raw: dict[str, Any]) -> tuple[set[int], set[int]]:
    radiant: set[int] = set()
    dire: set[int] = set()
    players = raw.get("players") if isinstance(raw.get("players"), list) else []
    for player in players:
        if not isinstance(player, dict):
            continue
        account_id = _positive_int(player.get("account_id"))
        if account_id <= 0:
            continue
        if player.get("team") == 0:
            radiant.add(account_id)
        elif player.get("team") == 1:
            dire.add(account_id)
    return radiant, dire


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


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
    matched: LiveRecordMatch,
    hero_map: dict[int, dict[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    raw = matched.raw
    team_a_side = matched.team_a_side
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
        "identity_method": matched.identity_method,
        "source_note": (
            "OpenDota live feed exposes current hero picks but not bans or original draft order. "
            + (
                "The anonymous live row was matched by exact 5v5 Steam account IDs from manually mapped OpenDota teams."
                if matched.identity_method == "verified_5v5_account_ids"
                else "The live row was matched by exact canonical team names."
            )
        ),
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


def _team_identity(value: str) -> str:
    return normalize_lookup_key(normalize_team_name(value))


def _build_report(
    generated_at: datetime,
    contexts: dict[str, dict[str, Any]],
    availability: dict[str, dict[str, Any]],
    live_records_seen: int,
    anonymous_live_records: int,
    identity_fallback_attempts: int,
    identity_fallback_matches: int,
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
        "anonymous_live_records": anonymous_live_records,
        "identity_fallback_attempts": identity_fallback_attempts,
        "identity_fallback_matches": identity_fallback_matches,
        "training_changed": False,
        "prediction_changed": False,
        "warnings": warnings,
        "errors": errors,
        "matches": contexts,
        "availability": availability,
    }


def _unavailable_context(match: Match, reason: str | None) -> dict[str, Any]:
    messages = {
        "opendota_team_mapping_missing": "A verified OpenDota team mapping is missing, so anonymous live picks cannot be linked safely.",
        "opendota_current_roster_fetch_failed": "OpenDota current roster data could not be loaded, so live picks were not linked.",
        "opendota_current_roster_not_exactly_five": "OpenDota did not return an exact five-player current roster for both teams.",
        "no_exact_5v5_account_identity_match": "OpenDota live rows have no team names and none matched both verified five-player rosters exactly.",
        "verified_source_mappings_unavailable": "Verified source mappings are unavailable, so anonymous live rows were not linked.",
        "verified_roster_identity_unavailable": "Verified five-player roster identity is unavailable for this live match.",
    }
    normalized_reason = reason or "no_verified_live_identity_match"
    return {
        "status": "unavailable",
        "reason": normalized_reason,
        "message": messages.get(
            normalized_reason,
            "No OpenDota live row could be matched to both teams with verified identity.",
        ),
        "identity_method": None,
        "team_a": match.team_a.name,
        "team_b": match.team_b.name,
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
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.getenv("LIVE_CONTEXT_REFRESH_INTERVAL_SECONDS", "60")),
    )
    args = parser.parse_args()

    while True:
        try:
            if args.compact:
                with redirect_stdout(StringIO()):
                    report = sync_live_match_context()
                print(
                    json.dumps(
                        {
                            key: report.get(key)
                            for key in (
                                "status",
                                "generated_at",
                                "live_records_seen",
                                "matched_live_matches",
                                "drafts_available",
                                "identity_fallback_matches",
                                "warnings",
                                "errors",
                            )
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            else:
                sync_live_match_context()
        except Exception as exc:
            # Keep the optional display-only feed alive across transient source failures.
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "generated_at": datetime.now(UTC).isoformat(),
                        "source": "opendota_live",
                        "errors": [f"{exc.__class__.__name__}: {exc}"],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                flush=True,
            )
        if args.once:
            return
        time.sleep(max(30, args.interval_seconds))


if __name__ == "__main__":
    main()
