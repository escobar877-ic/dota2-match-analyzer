from __future__ import annotations

import argparse
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

from app.db.models import Match
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.cross_source_match_resolver import find_possible_duplicate_matches
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.pro_match_quality import validate_verified_pro_match
from worker.data_ingestion.normalizer import (
    NormalizedMatch,
    normalize_opendota_matches,
    normalize_pandascore_matches,
    normalize_stratz_matches,
)
from worker.data_ingestion.source_mapping import (
    load_source_mappings,
    resolve_source_team,
    resolve_source_tournament,
)
from worker.data_ingestion.sources import get_source_client, get_source_clients
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


HISTORICAL_SYNC_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "historical_sync_report.json"


def sync_historical_matches(
    *,
    source: str,
    start_date: str,
    end_date: str,
    dry_run: bool = True,
    limit: int | None = None,
    tier1_only: bool = True,
    allow_empty_apply: bool = False,
    source_mode: str | None = None,
    artifact_path: str | Path | None = HISTORICAL_SYNC_REPORT_PATH,
) -> dict[str, Any]:
    db = get_session()
    matcher = Tier1Matcher()
    mappings = load_source_mappings()
    counters = SyncCounters()
    exclusion_reasons: Counter[str] = Counter()
    source_errors: list[str] = []
    warnings: list[str] = []
    excluded_samples: list[dict[str, Any]] = []
    duplicate_warnings = 0
    would_create = 0
    would_update = 0
    started_at = datetime.now(UTC)
    effective_source_mode = source_mode or ("discovery" if source == "opendota" else "trusted")
    source_confidence = "low" if source == "opendota" and effective_source_mode == "discovery" else "medium"
    try:
        clients = get_source_clients() if source == "all" else [get_source_client(source)]
        for client in clients:
            if not client.is_enabled():
                source_errors.append(f"{client.source_name}: {client.get_status().get('missing_key_reason') or 'disabled'}")
                continue
            result = client.fetch_matches(start_date=start_date, end_date=end_date, tier1_only=tier1_only, limit=limit or 100)
            if not result.ok:
                source_errors.append(f"{client.source_name}: {result.error}")
                continue
            normalized = _normalize(client.source_name, result.records)
            if limit is not None:
                normalized = normalized[:limit]
            counters.records_seen += len(normalized)
            for match in normalized:
                match = _apply_source_mappings(match, mappings)
                forced_reasons = _source_scope_reasons(match, effective_source_mode)
                quality_reasons = _quality_reasons_for_historical_sync(match, matcher)
                reasons = [_review_reason(reason) for reason in [*quality_reasons, *forced_reasons]]
                if reasons:
                    counters.records_excluded += 1
                    exclusion_reasons.update(reasons)
                    _append_excluded_sample(excluded_samples, match, reasons)
                    _append_raw_warning(warnings, match)
                    continue
                if dry_run:
                    existing = _existing_match(db, match)
                    would_update += int(existing is not None)
                    would_create += int(existing is None)
                    counters.records_created += int(existing is None)
                    counters.records_updated += int(existing is not None)
                    continue
                db_match, was_created = upsert_match(db, match, matcher=matcher, quality_scope="verified_pro")
                if db_match is None:
                    counters.records_excluded += 1
                    exclusion_reasons.update(["not_tier1_match"])
                    continue
                duplicates = find_possible_duplicate_matches(db, db_match)
                duplicate_warnings += len(duplicates)
                counters.records_created += int(was_created)
                counters.records_updated += int(not was_created)
        valid_rows = counters.records_created + counters.records_updated if not dry_run else would_create + would_update
        apply_allowed, apply_block_reason = _apply_status(source, effective_source_mode, source_confidence, valid_rows)
        if not dry_run and not allow_empty_apply and not apply_allowed:
            db.rollback()
            source_errors.append(f"apply blocked: {apply_block_reason}")
            report = _build_report(
                source=source,
                start_date=start_date,
                end_date=end_date,
                dry_run=dry_run,
                counters=counters,
                would_create=0,
                would_update=0,
                duplicate_warnings=duplicate_warnings,
                source_errors=source_errors,
                exclusion_reasons=exclusion_reasons,
                excluded_samples=excluded_samples,
                warnings=warnings,
                source_mode=effective_source_mode,
                source_confidence=source_confidence,
                apply_allowed=apply_allowed,
                apply_block_reason=apply_block_reason,
                recommendation=_recommendation(dry_run, source, source_errors, counters.records_excluded, valid_rows),
            )
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
            return report
        if dry_run:
            db.rollback()
        else:
            write_sync_log(
                db,
                source=source,
                sync_type="historical_matches",
                status="failed" if source_errors and counters.records_seen == 0 else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors) if source_errors else None,
                metadata_json={"exclusion_reasons": dict(exclusion_reasons), "duplicate_warnings": duplicate_warnings},
            )
            db.commit()
        recommendation = _recommendation(dry_run, source, source_errors, counters.records_excluded, valid_rows)
        report = _build_report(
            source=source,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            counters=counters,
            would_create=would_create,
            would_update=would_update,
            duplicate_warnings=duplicate_warnings,
            source_errors=source_errors,
            exclusion_reasons=exclusion_reasons,
            excluded_samples=excluded_samples,
            warnings=warnings,
            source_mode=effective_source_mode,
            source_confidence=source_confidence,
            apply_allowed=apply_allowed,
            apply_block_reason=apply_block_reason,
            recommendation=recommendation,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    except Exception as exc:
        db.rollback()
        report = {
            "status": "warning",
            "mode": "dry_run" if dry_run else "apply",
            "source": source,
            "records_seen": counters.records_seen,
            "source_errors": [str(exc)],
            "recommendation": "review_source_errors",
        }
        if artifact_path is not None:
            Path(artifact_path).parent.mkdir(parents=True, exist_ok=True)
            Path(artifact_path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def _normalize(source: str, records: list[Any]):
    if source == "opendota":
        return normalize_opendota_matches(records)
    if source == "stratz":
        return normalize_stratz_matches(records)
    if source == "pandascore":
        return normalize_pandascore_matches(records)
    return []


def _apply_source_mappings(match: NormalizedMatch, mappings: dict[str, Any]) -> NormalizedMatch:
    team_a_name = (
        resolve_source_team(match.external_source, match.team_a_external_id, match.team_a_name, mappings)
        or match.team_a_name
    )
    team_b_name = (
        resolve_source_team(match.external_source, match.team_b_external_id, match.team_b_name, mappings)
        or match.team_b_name
    )
    tournament_name = (
        resolve_source_tournament(match.external_source, None, match.tournament_name, mappings)
        or match.tournament_name
    )
    if team_a_name == match.team_a_name and team_b_name == match.team_b_name and tournament_name == match.tournament_name:
        return match
    return NormalizedMatch(
        external_source=match.external_source,
        external_id=match.external_id,
        team_a_external_id=match.team_a_external_id,
        team_b_external_id=match.team_b_external_id,
        team_a_name=team_a_name,
        team_b_name=team_b_name,
        tournament_name=tournament_name,
        tournament_tier=match.tournament_tier,
        start_time=match.start_time,
        format=match.format,
        status=match.status,
        winner_team_external_id=match.winner_team_external_id,
        raw_team_a=match.raw_team_a,
        raw_team_b=match.raw_team_b,
        raw_team_a_id=match.raw_team_a_id,
        raw_team_b_id=match.raw_team_b_id,
        raw_tournament=match.raw_tournament,
        raw_tournament_id=match.raw_tournament_id,
        is_draw=match.is_draw,
    )


def _review_reason(reason: str) -> str:
    return reason


def _source_scope_reasons(match: NormalizedMatch, source_mode: str) -> list[str]:
    reasons: list[str] = []
    if match.external_source == "opendota" and source_mode == "discovery":
        reasons.append("opendota_generic_discovery_only")
        if match.status != "finished":
            reasons.append("opendota_unverified_upcoming")
    return reasons


def _quality_reasons_for_historical_sync(match: NormalizedMatch, matcher: Tier1Matcher) -> list[str]:
    if match.external_source in {"pandascore", "stratz", "csv_import"}:
        return validate_verified_pro_match(match).reasons
    return validate_match(match, matcher=matcher).reasons


def _existing_match(db, match: NormalizedMatch) -> Match | None:
    if not match.external_id:
        return None
    return db.scalar(select(Match).where(Match.external_source == match.external_source, Match.external_id == match.external_id))


def _append_excluded_sample(samples: list[dict[str, Any]], match: NormalizedMatch, reasons: list[str]) -> None:
    if len(samples) >= 50:
        return
    samples.append(
        {
            "external_source": match.external_source,
            "external_id": match.external_id,
            "raw_team_a": match.raw_team_a,
            "raw_team_b": match.raw_team_b,
            "raw_team_a_id": match.raw_team_a_id or match.team_a_external_id,
            "raw_team_b_id": match.raw_team_b_id or match.team_b_external_id,
            "raw_tournament": match.raw_tournament,
            "raw_tournament_id": match.raw_tournament_id,
            "normalized_team_a": match.team_a_name,
            "normalized_team_b": match.team_b_name,
            "normalized_tournament": match.tournament_name,
            "start_time": match.start_time.isoformat() if match.start_time else None,
            "status": match.status,
            "exclusion_reasons": reasons,
            "reasons": reasons,
        }
    )


def _append_raw_warning(warnings: list[str], match: NormalizedMatch) -> None:
    if match.external_source != "opendota":
        return
    if match.raw_team_a and match.raw_team_b and match.raw_tournament:
        return
    message = "OpenDota endpoint lacks team/tournament names; cannot verify Tier 1 without enrichment."
    if message not in warnings:
        warnings.append(message)


def _apply_status(source: str, source_mode: str, source_confidence: str, valid_rows: int) -> tuple[bool, str | None]:
    if source == "opendota" and source_mode == "discovery":
        return False, "OpenDota generic feed is discovery-only and not safe for apply."
    if source_confidence not in {"medium", "high"}:
        return False, "Source confidence is below medium."
    if valid_rows <= 0:
        return False, "No valid verified pro rows found."
    return True, None


def _recommendation(dry_run: bool, source: str, source_errors: list[str], excluded_count: int, valid_rows: int) -> str:
    if source == "opendota" and valid_rows == 0:
        return "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source"
    if source_errors:
        if source == "stratz" and any("date-range historical fetch is not implemented" in error for error in source_errors):
            return "use_stratz_match_ids_or_pandascore_schedule_or_csv_batch"
        return "review_source_errors"
    if dry_run and valid_rows == 0 and excluded_count > 0:
        return "mapping_or_alias_review_needed"
    if dry_run and valid_rows > 0:
        return "valid_rows_found_review_then_apply_with_explicit_flag"
    return "run_validation_audit_coverage"


def _build_report(
    *,
    source: str,
    start_date: str,
    end_date: str,
    dry_run: bool,
    counters: SyncCounters,
    would_create: int,
    would_update: int,
    duplicate_warnings: int,
    source_errors: list[str],
    exclusion_reasons: Counter[str],
    excluded_samples: list[dict[str, Any]],
    warnings: list[str],
    source_mode: str,
    source_confidence: str,
    apply_allowed: bool,
    apply_block_reason: str | None,
    recommendation: str,
) -> dict[str, Any]:
    valid_rows = would_create + would_update if dry_run else counters.records_created + counters.records_updated
    return {
        "status": "warning" if source_errors or counters.records_excluded else "ok",
        "mode": "dry_run" if dry_run else "apply",
        "source": source,
        "source_mode": source_mode,
        "source_trust_level": source_mode,
        "source_confidence": source_confidence,
        "quality_scope": "verified_pro",
        "apply_allowed": apply_allowed,
        "apply_block_reason": apply_block_reason,
        "start_date": start_date,
        "end_date": end_date,
        "records_seen": counters.records_seen,
        "would_create": would_create if dry_run else 0,
        "would_update": would_update if dry_run else 0,
        "would_exclude": counters.records_excluded if dry_run else 0,
        "records_created": counters.records_created if not dry_run else 0,
        "records_updated": counters.records_updated if not dry_run else 0,
        "records_excluded": counters.records_excluded,
        "valid_rows": valid_rows,
        "duplicate_warnings": duplicate_warnings,
        "source_errors": source_errors,
        "warnings": warnings,
        "exclusion_reasons": dict(exclusion_reasons),
        "excluded_samples": excluded_samples,
        "recommendation": recommendation,
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
    parser = argparse.ArgumentParser(description="Safely dry-run/apply historical Tier 1 match sync.")
    parser.add_argument("--source", choices=["opendota", "stratz", "pandascore", "all"], required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-empty-apply", action="store_true")
    parser.add_argument("--source-mode", choices=["discovery", "trusted"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tier1-only", default="true")
    args = parser.parse_args()
    sync_historical_matches(
        source=args.source,
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=not args.apply,
        limit=args.limit,
        tier1_only=str(args.tier1_only).lower() != "false",
        allow_empty_apply=args.allow_empty_apply,
        source_mode=args.source_mode,
    )


if __name__ == "__main__":
    main()
