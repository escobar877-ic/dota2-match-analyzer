from __future__ import annotations

import argparse
import csv
import json
import os
import sys
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

from sqlalchemy import select

from app.db.models import DraftSnapshot, Hero, Match, MatchDraft
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.normalizer import normalize_stratz_matches
from worker.data_ingestion.sources.stratz_client import StratzSourceClient
from worker.data_ingestion.stratz_match_id_validator import (
    STRATZ_MATCH_ID_VALIDATION_REPORT_PATH,
    validate_stratz_match_id_batch,
)
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


STRATZ_MATCH_ID_IMPORT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "stratz_match_id_import_report.json"
SOURCE = "stratz"


def import_stratz_match_id_batch(
    path: str | Path,
    *,
    apply: bool = False,
    limit: int | None = None,
    client: StratzSourceClient | None = None,
    artifact_path: str | Path | None = STRATZ_MATCH_ID_IMPORT_REPORT_PATH,
    validation_artifact_path: str | Path | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    db = get_session()
    counters = SyncCounters()
    warnings: list[str] = []
    source_errors: list[str] = []
    exclusion_reasons: Counter[str] = Counter()
    client = client or StratzSourceClient()
    try:
        validation = validate_stratz_match_id_batch(
            path,
            client=client,
            artifact_path=validation_artifact_path,
            limit=limit,
        )
        rows = _read_rows(path)
        if limit is not None:
            rows = rows[:limit]
        counters.records_seen = len(rows)

        if apply and not validation.get("safe_to_apply"):
            counters.records_excluded = len(rows)
            source_errors.append("apply blocked: STRATZ match ID validation is not safe_to_apply")
            write_sync_log(
                db,
                source=SOURCE,
                sync_type="stratz_match_id_import",
                status="failed",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors),
                metadata_json={"file": str(path), "validation_status": validation.get("status")},
            )
            db.rollback()
            report = _build_report(
                path=path,
                apply=apply,
                counters=counters,
                would_create=0,
                would_update=0,
                validation=validation,
                source_errors=source_errors,
                warnings=warnings,
                exclusion_reasons=exclusion_reasons,
                draft_imported_count=0,
            )
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
            return report

        valid_ids = set(validation.get("valid_match_ids") or [])
        would_create = 0
        would_update = 0
        draft_imported_count = 0
        matcher = Tier1Matcher()
        metadata_by_match: dict[str, Any] = {}

        for row in rows:
            match_id = (row.get("match_id") or "").strip()
            if match_id not in valid_ids:
                counters.records_excluded += 1
                exclusion_reasons.update(["validation_excluded"])
                continue
            result = client.fetch_match_details(match_id)
            if not result.ok or not result.records:
                counters.records_excluded += 1
                source_errors.append(f"match_id={match_id}: {result.error or 'STRATZ match not found'}")
                continue
            raw_match = result.records[0]
            normalized = normalize_stratz_matches([raw_match])
            if not normalized:
                counters.records_excluded += 1
                exclusion_reasons.update(["normalization_failed"])
                continue
            match = normalized[0]
            quality = validate_match(match, matcher=matcher)
            if quality.reasons or match.status != "finished":
                counters.records_excluded += 1
                exclusion_reasons.update(quality.reasons or ["match_not_finished"])
                continue

            existing = db.scalar(select(Match).where(Match.external_source == SOURCE, Match.external_id == match.external_id))
            if not apply:
                would_update += int(existing is not None)
                would_create += int(existing is None)
                continue

            db_match, was_created = upsert_match(db, match, matcher=matcher)
            if db_match is None:
                counters.records_excluded += 1
                exclusion_reasons.update(["not_tier1_match"])
                continue
            metadata_by_match[match_id] = _verification_metadata(row, raw_match)
            if _import_draft_if_available(db, db_match, match, raw_match):
                draft_imported_count += 1
            counters.records_created += int(was_created)
            counters.records_updated += int(not was_created)

        if apply:
            write_sync_log(
                db,
                source=SOURCE,
                sync_type="stratz_match_id_import",
                status="warning" if source_errors or counters.records_excluded else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors) if source_errors else None,
                metadata_json={
                    "file": str(path),
                    "validation_status": validation.get("status"),
                    "verification_metadata": metadata_by_match,
                    "exclusion_reasons": dict(exclusion_reasons),
                    "draft_imported_count": draft_imported_count,
                },
            )
            db.commit()
        else:
            db.rollback()
            counters.records_created = would_create
            counters.records_updated = would_update

        report = _build_report(
            path=path,
            apply=apply,
            counters=counters,
            would_create=would_create,
            would_update=would_update,
            validation=validation,
            source_errors=source_errors,
            warnings=warnings,
            exclusion_reasons=exclusion_reasons,
            draft_imported_count=draft_imported_count,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    except Exception as exc:
        db.rollback()
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "warning",
            "mode": "apply" if apply else "dry_run",
            "source": SOURCE,
            "file": str(path),
            "records_seen": counters.records_seen,
            "source_errors": [str(exc)],
            "safe_to_apply": False,
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _verification_metadata(row: dict[str, str], raw_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "verified_by_source": "stratz_match_id_batch",
        "source_confidence": "high",
        "source_url": (row.get("source_url") or "").strip(),
        "verification_note": (row.get("verification_note") or "").strip(),
        "expected_team_a_name": (row.get("expected_team_a_name") or "").strip(),
        "expected_team_b_name": (row.get("expected_team_b_name") or "").strip(),
        "expected_tournament_name": (row.get("expected_tournament_name") or "").strip(),
        "expected_start_date": (row.get("expected_start_date") or "").strip(),
        "duration": raw_match.get("durationSeconds") or raw_match.get("duration"),
        "patch_version": raw_match.get("gameVersion") or raw_match.get("gameVersionId") or raw_match.get("patch"),
        "draft_available": bool(_draft_entries(raw_match)),
    }


def _import_draft_if_available(db, db_match: Match, match, raw_match: dict[str, Any]) -> bool:
    entries = _draft_entries(raw_match)
    if not entries:
        return False
    existing_count = db.scalar(
        select(__import__("sqlalchemy").func.count(MatchDraft.id)).where(
            MatchDraft.match_id == db_match.id,
            MatchDraft.source == SOURCE,
        )
    )
    if existing_count:
        _upsert_snapshot(db, db_match.id, entries, match)
        return True

    imported = 0
    for index, entry in enumerate(entries, start=1):
        hero_external_id = _entry_hero_id(entry)
        team_external_id = _entry_team_id(entry)
        if hero_external_id is None or team_external_id is None:
            continue
        team_id = None
        side = "unknown"
        if str(team_external_id) == str(match.team_a_external_id):
            team_id = db_match.team_a_id
            side = "radiant"
        elif str(team_external_id) == str(match.team_b_external_id):
            team_id = db_match.team_b_id
            side = "dire"
        if team_id is None:
            continue
        hero = _ensure_hero(db, int(hero_external_id))
        action_type = _entry_action_type(entry)
        db.add(
            MatchDraft(
                match_id=db_match.id,
                team_id=team_id,
                hero_id=hero.id,
                action_type=action_type,
                pick_order=index if action_type == "pick" else None,
                ban_order=index if action_type == "ban" else None,
                draft_order=int(entry.get("order") or entry.get("draftOrder") or index),
                side=side,
                source=SOURCE,
            )
        )
        imported += 1
    _upsert_snapshot(db, db_match.id, entries, match)
    return imported > 0


def _ensure_hero(db, hero_id: int) -> Hero:
    hero = db.scalar(select(Hero).where(Hero.hero_id == hero_id))
    if hero:
        return hero
    hero = Hero(hero_id=hero_id, name=f"hero_{hero_id}", localized_name=f"Hero {hero_id}", is_active=True)
    db.add(hero)
    db.flush()
    return hero


def _upsert_snapshot(db, match_id: int, entries: list[dict[str, Any]], match) -> None:
    team_a_picks = _count_entries(entries, match.team_a_external_id, "pick")
    team_b_picks = _count_entries(entries, match.team_b_external_id, "pick")
    team_a_bans = _count_entries(entries, match.team_a_external_id, "ban")
    team_b_bans = _count_entries(entries, match.team_b_external_id, "ban")
    snapshot = db.scalar(select(DraftSnapshot).where(DraftSnapshot.match_id == match_id, DraftSnapshot.source == SOURCE))
    if snapshot is None:
        snapshot = DraftSnapshot(match_id=match_id, source=SOURCE)
        db.add(snapshot)
    snapshot.team_a_picks_count = team_a_picks
    snapshot.team_b_picks_count = team_b_picks
    snapshot.team_a_bans_count = team_a_bans
    snapshot.team_b_bans_count = team_b_bans
    snapshot.draft_complete = team_a_picks >= 5 and team_b_picks >= 5


def _count_entries(entries: list[dict[str, Any]], team_id: str, action_type: str) -> int:
    return sum(1 for entry in entries if str(_entry_team_id(entry)) == str(team_id) and _entry_action_type(entry) == action_type)


def _draft_entries(raw_match: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("pickBans", "picksBans", "draftEntries", "draft"):
        value = raw_match.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _entry_hero_id(entry: dict[str, Any]) -> Any:
    return entry.get("heroId") or entry.get("hero_id") or entry.get("hero")


def _entry_team_id(entry: dict[str, Any]) -> Any:
    return entry.get("teamId") or entry.get("team_id")


def _entry_action_type(entry: dict[str, Any]) -> str:
    if entry.get("isPick") is True:
        return "pick"
    if entry.get("isPick") is False:
        return "ban"
    value = str(entry.get("actionType") or entry.get("type") or "").lower()
    return "ban" if "ban" in value else "pick"


def _build_report(
    *,
    path: str | Path,
    apply: bool,
    counters: SyncCounters,
    would_create: int,
    would_update: int,
    validation: dict[str, Any],
    source_errors: list[str],
    warnings: list[str],
    exclusion_reasons: Counter[str],
    draft_imported_count: int,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "warning" if source_errors or counters.records_excluded or validation.get("status") != "ok" else "ok",
        "mode": "apply" if apply else "dry_run",
        "source": SOURCE,
        "file": str(path),
        "records_seen": counters.records_seen,
        "would_create": would_create if not apply else 0,
        "would_update": would_update if not apply else 0,
        "would_exclude": counters.records_excluded if not apply else 0,
        "records_created": counters.records_created if apply else 0,
        "records_updated": counters.records_updated if apply else 0,
        "records_excluded": counters.records_excluded,
        "validation_status": validation.get("status"),
        "safe_to_apply": bool(validation.get("safe_to_apply")),
        "source_errors": source_errors,
        "warnings": [*warnings, *(validation.get("warnings") or [])],
        "exclusion_reasons": dict(exclusion_reasons),
        "draft_imported_count": draft_imported_count,
        "apply_allowed": bool(validation.get("safe_to_apply")),
        "apply_block_reason": None if validation.get("safe_to_apply") else "STRATZ match ID validation is not safe_to_apply.",
    }


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run/apply manually verified STRATZ match ID batches.")
    parser.add_argument("--file", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    import_stratz_match_id_batch(
        args.file,
        apply=args.apply,
        limit=args.limit,
        validation_artifact_path=STRATZ_MATCH_ID_VALIDATION_REPORT_PATH,
    )


if __name__ == "__main__":
    main()
