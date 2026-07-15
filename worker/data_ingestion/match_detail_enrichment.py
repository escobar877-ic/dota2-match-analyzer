from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.db.models import DraftSnapshot, Hero, Match, MatchDraft, Team, TeamMatchStats
from app.ratings.team_identity import canonical_team_identity_name
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


DETAIL_SOURCE = "opendota_detail"
REPORT_PATH = Path(ML_ARTIFACT_DIR) / "match_detail_enrichment_report.json"


def enrich_match_details(
    *,
    apply: bool = False,
    limit: int = 50,
    offset: int = 0,
    team: str | None = None,
    tournament: str | None = None,
    sleep_seconds: float = 1.0,
    rate_limit_retries: int = 2,
    rate_limit_backoff_seconds: float = 30.0,
    force: bool = False,
    client: OpenDotaClient | None = None,
    artifact_path: str | Path | None = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    db = get_session()
    client = client or OpenDotaClient()
    counters = SyncCounters()
    source_errors: list[str] = []
    warnings: list[str] = []
    exclusion_reasons: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    details_fetched = 0
    skipped_existing = 0
    rate_limit_retries_used = 0
    would_enrich = 0
    stats_rows_created = 0
    stats_rows_updated = 0
    draft_entries_created = 0
    draft_entries_updated = 0
    draft_snapshots_created = 0
    draft_snapshots_updated = 0

    try:
        statement = (
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(
                Match.external_source == "csv_import",
                Match.dataset_profile == "historical_training",
                Match.status == "finished",
                Match.verification_status == "verified",
                Match.source_confidence.in_(["high", "medium"]),
                Match.external_id.is_not(None),
            )
        )
        if team:
            pattern = f"%{team.strip()}%"
            statement = statement.where(
                or_(Match.team_a.has(Team.name.ilike(pattern)), Match.team_b.has(Team.name.ilike(pattern)))
            )
        if tournament:
            statement = statement.where(Match.tournament_name.ilike(f"%{tournament.strip()}%"))
        matches = list(
            db.scalars(
                statement
                .order_by(Match.start_time.desc().nullslast(), Match.id.desc())
                .offset(max(0, offset))
                .limit(max(1, min(limit, 1000)))
            )
        )
        counters.records_seen = len(matches)

        for index, match in enumerate(matches):
            if not force and _is_enrichment_complete(db, match.id):
                skipped_existing += 1
                continue
            if not match.external_id or not match.external_id.isdigit():
                counters.records_excluded += 1
                exclusion_reasons.update(["invalid_dota_match_id"])
                continue

            response, retry_warnings, retries_used = _fetch_match_detail(
                client,
                match.external_id,
                max_retries=rate_limit_retries,
                backoff_seconds=rate_limit_backoff_seconds,
            )
            warnings.extend(retry_warnings)
            rate_limit_retries_used += retries_used
            if not response.ok or not isinstance(response.data, dict):
                counters.records_excluded += 1
                message = response.error or "OpenDota match detail response is invalid."
                source_errors.append(f"match_id={match.external_id}: {message}")
                if index < len(matches) - 1 and sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue

            raw = response.data
            details_fetched += 1
            validation_errors, side_to_team_id = _validate_detail(match, raw)
            if validation_errors:
                counters.records_excluded += 1
                exclusion_reasons.update(validation_errors)
                if len(samples) < 20:
                    samples.append(_sample(match, raw, validation_errors))
                if index < len(matches) - 1 and sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue

            players = raw.get("players") if isinstance(raw.get("players"), list) else []
            draft = raw.get("picks_bans") if isinstance(raw.get("picks_bans"), list) else []
            if len(players) < 10:
                counters.records_excluded += 1
                exclusion_reasons.update(["incomplete_player_stats"])
                if len(samples) < 20:
                    samples.append(_sample(match, raw, ["incomplete_player_stats"]))
                if index < len(matches) - 1 and sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue
            if not draft:
                warnings.append(f"match_id={match.external_id}: draft unavailable; stats can still be enriched")

            would_enrich += 1
            if apply:
                stats_result = _upsert_team_stats(db, match, raw, players, side_to_team_id)
                draft_result = _upsert_draft(db, match, draft, side_to_team_id)
                stats_rows_created += stats_result["created"]
                stats_rows_updated += stats_result["updated"]
                draft_entries_created += draft_result["created"]
                draft_entries_updated += draft_result["updated"]
                draft_snapshots_created += draft_result["snapshot_created"]
                draft_snapshots_updated += draft_result["snapshot_updated"]
                counters.records_updated += 1

            if len(samples) < 20:
                samples.append(_sample(match, raw, []))
            if index < len(matches) - 1 and sleep_seconds > 0:
                time.sleep(sleep_seconds)

        status = "warning" if source_errors or counters.records_excluded else "ok"
        if apply:
            write_sync_log(
                db,
                source="opendota",
                sync_type="match_detail_enrichment",
                status=status,
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors[:20]) if source_errors else None,
                metadata_json={
                    "details_fetched": details_fetched,
                    "skipped_existing": skipped_existing,
                    "rate_limit_retries_used": rate_limit_retries_used,
                    "stats_rows_created": stats_rows_created,
                    "stats_rows_updated": stats_rows_updated,
                    "draft_entries_created": draft_entries_created,
                    "draft_entries_updated": draft_entries_updated,
                    "exclusion_reasons": dict(exclusion_reasons),
                    "training_changed": False,
                    "promotion_changed": False,
                },
            )
        else:
            db.rollback()

        totals = _enrichment_totals(db)

        report = {
            "status": status,
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "apply" if apply else "dry_run",
            "source": "opendota",
            "scope": "verified_historical_match_details",
            "filters": {"team": team, "tournament": tournament},
            "records_seen": counters.records_seen,
            "details_fetched": details_fetched,
            "would_enrich": would_enrich if not apply else 0,
            "matches_enriched": counters.records_updated if apply else 0,
            "records_excluded": counters.records_excluded,
            "skipped_existing": skipped_existing,
            "rate_limit_retries_used": rate_limit_retries_used,
            "stats_rows_created": stats_rows_created,
            "stats_rows_updated": stats_rows_updated,
            "draft_entries_created": draft_entries_created,
            "draft_entries_updated": draft_entries_updated,
            "draft_snapshots_created": draft_snapshots_created,
            "draft_snapshots_updated": draft_snapshots_updated,
            **totals,
            "exclusion_reasons": dict(exclusion_reasons),
            "source_errors": source_errors,
            "warnings": _unique(warnings),
            "samples": samples,
            "apply_allowed": would_enrich > 0,
            "training_changed": False,
            "promotion_changed": False,
            "recommendation": "apply_after_review" if not apply and would_enrich else "enrichment_complete",
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    except Exception as exc:
        db.rollback()
        counters.records_excluded += 1
        report = {
            "status": "failed",
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "apply" if apply else "dry_run",
            "records_seen": counters.records_seen,
            "records_excluded": counters.records_excluded,
            "source_errors": [f"{exc.__class__.__name__}: {exc}"],
            "apply_allowed": False,
            "training_changed": False,
            "promotion_changed": False,
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def _fetch_match_detail(
    client: OpenDotaClient,
    external_id: str,
    *,
    max_retries: int,
    backoff_seconds: float,
) -> tuple[Any, list[str], int]:
    warnings: list[str] = []
    retries_used = 0
    response = client.get_match(external_id)
    for attempt in range(max(0, max_retries)):
        if response.ok or "HTTP 429" not in (response.error or ""):
            break
        wait_seconds = max(1.0, backoff_seconds) * (attempt + 1)
        retries_used += 1
        warnings.append(
            f"match_id={external_id}: OpenDota rate limit reached; retrying after {wait_seconds:.0f}s"
        )
        time.sleep(wait_seconds)
        response = client.get_match(external_id)
    return response, warnings, retries_used


def _validate_detail(match: Match, raw: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    if str(raw.get("match_id") or "") != str(match.external_id):
        errors.append("match_id_mismatch")
    if _positive_int(raw.get("duration")) <= 0:
        errors.append("invalid_duration")
    if not isinstance(raw.get("radiant_win"), bool):
        errors.append("winner_missing")

    radiant = raw.get("radiant_team") if isinstance(raw.get("radiant_team"), dict) else {}
    dire = raw.get("dire_team") if isinstance(raw.get("dire_team"), dict) else {}
    radiant_external_id = str(raw.get("radiant_team_id") or radiant.get("team_id") or "")
    dire_external_id = str(raw.get("dire_team_id") or dire.get("team_id") or "")
    radiant_name = str(radiant.get("name") or raw.get("radiant_name") or "")
    dire_name = str(dire.get("name") or raw.get("dire_name") or "")

    team_a_matches_radiant = _team_matches(match.team_a.external_id, match.team_a.name, radiant_external_id, radiant_name)
    team_b_matches_dire = _team_matches(match.team_b.external_id, match.team_b.name, dire_external_id, dire_name)
    team_a_matches_dire = _team_matches(match.team_a.external_id, match.team_a.name, dire_external_id, dire_name)
    team_b_matches_radiant = _team_matches(match.team_b.external_id, match.team_b.name, radiant_external_id, radiant_name)

    if team_a_matches_radiant and team_b_matches_dire:
        side_to_team_id = {"radiant": match.team_a_id, "dire": match.team_b_id}
    elif team_a_matches_dire and team_b_matches_radiant:
        side_to_team_id = {"radiant": match.team_b_id, "dire": match.team_a_id}
    else:
        errors.append("team_identity_mismatch")
        side_to_team_id = {}

    if side_to_team_id and isinstance(raw.get("radiant_win"), bool) and match.winner_team_id is not None:
        raw_winner_id = side_to_team_id["radiant" if raw["radiant_win"] else "dire"]
        if raw_winner_id != match.winner_team_id:
            errors.append("winner_mismatch")
    return _unique(errors), side_to_team_id


def _team_matches(
    expected_external_id: str | None,
    expected_name: str,
    raw_external_id: str,
    raw_name: str,
) -> bool:
    if expected_external_id and raw_external_id and str(expected_external_id) == str(raw_external_id):
        return True
    return bool(raw_name and canonical_team_identity_name(expected_name) == canonical_team_identity_name(raw_name))


def _upsert_team_stats(
    db,
    match: Match,
    raw: dict[str, Any],
    players: list[dict[str, Any]],
    side_to_team_id: dict[str, int],
) -> dict[str, int]:
    radiant_players = [player for player in players if _is_radiant_player(player)]
    dire_players = [player for player in players if not _is_radiant_player(player)]
    radiant_gold_10 = _team_timeline_value(radiant_players, "gold_t", 10)
    dire_gold_10 = _team_timeline_value(dire_players, "gold_t", 10)
    radiant_xp_10 = _team_timeline_value(radiant_players, "xp_t", 10)
    dire_xp_10 = _team_timeline_value(dire_players, "xp_t", 10)
    gold_diff = _difference(radiant_gold_10, dire_gold_10)
    xp_diff = _difference(radiant_xp_10, dire_xp_10)
    duration = _positive_int(raw.get("duration"))
    radiant_win = bool(raw.get("radiant_win"))
    result = {"created": 0, "updated": 0}

    for side, side_players in (("radiant", radiant_players), ("dire", dire_players)):
        team_id = side_to_team_id[side]
        stats = db.scalar(
            select(TeamMatchStats)
            .where(TeamMatchStats.match_id == match.id, TeamMatchStats.team_id == team_id)
            .order_by(TeamMatchStats.id.asc())
            .limit(1)
        )
        if stats is None:
            stats = TeamMatchStats(match_id=match.id, team_id=team_id)
            db.add(stats)
            result["created"] += 1
        else:
            result["updated"] += 1
        stats.side = side
        stats.kills = sum(_non_negative_int(player.get("kills")) for player in side_players)
        stats.deaths = sum(_non_negative_int(player.get("deaths")) for player in side_players)
        stats.assists = sum(_non_negative_int(player.get("assists")) for player in side_players)
        stats.gold_diff_10 = gold_diff if side == "radiant" else (-gold_diff if gold_diff is not None else None)
        stats.xp_diff_10 = xp_diff if side == "radiant" else (-xp_diff if xp_diff is not None else None)
        stats.duration = duration
        won = radiant_win if side == "radiant" else not radiant_win
        stats.result = "win" if won else "loss"
    return result


def _upsert_draft(
    db,
    match: Match,
    entries: list[dict[str, Any]],
    side_to_team_id: dict[str, int],
) -> dict[str, int]:
    result = {"created": 0, "updated": 0, "snapshot_created": 0, "snapshot_updated": 0}
    counts: dict[tuple[int, str], int] = Counter()
    normalized_entries: list[dict[str, Any]] = []
    for fallback_order, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        side = "radiant" if entry.get("team") in {0, "0", "radiant"} else "dire" if entry.get("team") in {1, "1", "dire"} else None
        hero_external_id = _positive_int(entry.get("hero_id") or entry.get("heroId"))
        if side is None or hero_external_id <= 0:
            continue
        action_type = "pick" if entry.get("is_pick") is True or entry.get("isPick") is True else "ban"
        team_id = side_to_team_id[side]
        counts[(team_id, action_type)] += 1
        normalized_entries.append(
            {
                "team_id": team_id,
                "side": side,
                "hero_external_id": hero_external_id,
                "action_type": action_type,
                "action_order": counts[(team_id, action_type)],
                "draft_order": _non_negative_int(entry.get("order"), default=fallback_order),
            }
        )

    for item in normalized_entries:
        hero = _ensure_hero(db, item["hero_external_id"])
        draft_entry = db.scalar(
            select(MatchDraft)
            .where(
                MatchDraft.match_id == match.id,
                MatchDraft.source == DETAIL_SOURCE,
                MatchDraft.draft_order == item["draft_order"],
            )
            .order_by(MatchDraft.id.asc())
            .limit(1)
        )
        if draft_entry is None:
            draft_entry = MatchDraft(
                match_id=match.id,
                team_id=item["team_id"],
                hero_id=hero.id,
                action_type=item["action_type"],
                draft_order=item["draft_order"],
                side=item["side"],
                source=DETAIL_SOURCE,
            )
            db.add(draft_entry)
            result["created"] += 1
        else:
            result["updated"] += 1
            draft_entry.team_id = item["team_id"]
            draft_entry.hero_id = hero.id
            draft_entry.action_type = item["action_type"]
            draft_entry.side = item["side"]
        draft_entry.pick_order = item["action_order"] if item["action_type"] == "pick" else None
        draft_entry.ban_order = item["action_order"] if item["action_type"] == "ban" else None

    snapshot = db.scalar(
        select(DraftSnapshot)
        .where(DraftSnapshot.match_id == match.id, DraftSnapshot.source == DETAIL_SOURCE)
        .order_by(DraftSnapshot.id.asc())
        .limit(1)
    )
    if snapshot is None:
        snapshot = DraftSnapshot(match_id=match.id, source=DETAIL_SOURCE)
        db.add(snapshot)
        result["snapshot_created"] = 1
    else:
        result["snapshot_updated"] = 1
    snapshot.team_a_picks_count = counts[(match.team_a_id, "pick")]
    snapshot.team_b_picks_count = counts[(match.team_b_id, "pick")]
    snapshot.team_a_bans_count = counts[(match.team_a_id, "ban")]
    snapshot.team_b_bans_count = counts[(match.team_b_id, "ban")]
    snapshot.draft_complete = snapshot.team_a_picks_count >= 5 and snapshot.team_b_picks_count >= 5
    return result


def _ensure_hero(db, hero_external_id: int) -> Hero:
    hero = db.scalar(select(Hero).where(Hero.hero_id == hero_external_id))
    if hero is not None:
        return hero
    hero = Hero(
        hero_id=hero_external_id,
        name=f"hero_{hero_external_id}",
        localized_name=f"Hero {hero_external_id}",
        is_active=True,
    )
    db.add(hero)
    db.flush()
    return hero


def _is_enrichment_complete(db, match_id: int) -> bool:
    stats_count = db.scalar(select(func.count(TeamMatchStats.id)).where(TeamMatchStats.match_id == match_id)) or 0
    snapshot_exists = db.scalar(
        select(DraftSnapshot.id)
        .where(DraftSnapshot.match_id == match_id, DraftSnapshot.source == DETAIL_SOURCE)
        .limit(1)
    )
    return stats_count >= 2 and snapshot_exists is not None


def _enrichment_totals(db) -> dict[str, int]:
    scope = (
        Match.external_source == "csv_import",
        Match.dataset_profile == "historical_training",
        Match.status == "finished",
        Match.verification_status == "verified",
        Match.source_confidence.in_(["high", "medium"]),
    )
    total_enriched_matches = db.scalar(
        select(func.count(func.distinct(TeamMatchStats.match_id)))
        .join(Match, Match.id == TeamMatchStats.match_id)
        .where(*scope)
    ) or 0
    total_stats_rows = db.scalar(
        select(func.count(TeamMatchStats.id))
        .join(Match, Match.id == TeamMatchStats.match_id)
        .where(*scope)
    ) or 0
    total_draft_entries = db.scalar(
        select(func.count(MatchDraft.id))
        .join(Match, Match.id == MatchDraft.match_id)
        .where(*scope, MatchDraft.source == DETAIL_SOURCE)
    ) or 0
    total_draft_snapshots = db.scalar(
        select(func.count(DraftSnapshot.id))
        .join(Match, Match.id == DraftSnapshot.match_id)
        .where(*scope, DraftSnapshot.source == DETAIL_SOURCE)
    ) or 0
    return {
        "total_enriched_matches": int(total_enriched_matches),
        "total_stats_rows": int(total_stats_rows),
        "total_draft_entries": int(total_draft_entries),
        "total_draft_snapshots": int(total_draft_snapshots),
    }


def _is_radiant_player(player: dict[str, Any]) -> bool:
    if isinstance(player.get("isRadiant"), bool):
        return bool(player["isRadiant"])
    return _non_negative_int(player.get("player_slot")) < 128


def _team_timeline_value(players: list[dict[str, Any]], field: str, minute: int) -> int | None:
    values: list[int] = []
    for player in players:
        timeline = player.get(field)
        if not isinstance(timeline, list) or len(timeline) <= minute:
            continue
        try:
            values.append(int(timeline[minute]))
        except (TypeError, ValueError):
            continue
    return sum(values) if values else None


def _difference(value_a: int | None, value_b: int | None) -> int | None:
    if value_a is None or value_b is None:
        return None
    return value_a - value_b


def _sample(match: Match, raw: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    players = raw.get("players") if isinstance(raw.get("players"), list) else []
    draft = raw.get("picks_bans") if isinstance(raw.get("picks_bans"), list) else []
    return {
        "database_match_id": match.id,
        "dota_match_id": match.external_id,
        "team_a": match.team_a.name,
        "team_b": match.team_b.name,
        "tournament": match.tournament_name,
        "start_time": match.start_time.isoformat() if match.start_time else None,
        "duration": raw.get("duration"),
        "players_count": len(players),
        "draft_entries_count": len(draft),
        "validation_errors": errors,
    }


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _non_negative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich verified historical matches with OpenDota stats and drafts.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--team")
    parser.add_argument("--tournament")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--rate-limit-retries", type=int, default=2)
    parser.add_argument("--rate-limit-backoff", type=float, default=30.0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    enrich_match_details(
        apply=args.apply,
        limit=args.limit,
        offset=args.offset,
        team=args.team,
        tournament=args.tournament,
        sleep_seconds=max(0.0, args.sleep),
        rate_limit_retries=max(0, args.rate_limit_retries),
        rate_limit_backoff_seconds=max(1.0, args.rate_limit_backoff),
        force=args.force,
    )


if __name__ == "__main__":
    main()
