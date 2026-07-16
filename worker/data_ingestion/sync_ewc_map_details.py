from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import case, select
from sqlalchemy.orm import selectinload


backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from app.db.models import Match
from app.ratings.team_identity import canonical_team_identity_name
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.import_stratz_ids import classify_training_match
from worker.data_ingestion.match_detail_enrichment import enrich_match_details
from worker.data_ingestion.normalizer import NormalizedMatch, normalize_datetime
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.source_mapping import resolve_source_team, resolve_source_tournament
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


EWC_LEAGUE_ID = "19785"
REPORT_PATH = Path(ML_ARTIFACT_DIR) / "ewc_map_details_sync_report.json"
MAP_SOURCES = {"csv_import", "opendota", "stratz"}


def sync_ewc_map_details(
    *,
    apply: bool = False,
    limit: int = 300,
    enrich_limit: int = 20,
    sleep_seconds: float = 0.5,
    client: OpenDotaClient | None = None,
    artifact_path: str | Path | None = REPORT_PATH,
    enricher: Callable[..., dict[str, Any]] = enrich_match_details,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    client = client or OpenDotaClient()
    matcher = Tier1Matcher()
    db = get_session()
    counters = SyncCounters()
    classifications: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    errors: list[str] = []
    warnings: list[str] = []
    valid_external_ids: list[str] = []
    new_external_ids: list[str] = []
    samples: list[dict[str, Any]] = []
    would_create = 0
    would_update = 0
    enrichment: dict[str, Any] | None = None

    try:
        response = client.get_league_matches(EWC_LEAGUE_ID)
        if not response.ok:
            errors.append(response.error or "OpenDota EWC league request failed.")
            return _finish(
                apply=apply,
                counters=counters,
                classifications=classifications,
                exclusion_reasons=exclusion_reasons,
                errors=errors,
                warnings=warnings,
                would_create=0,
                would_update=0,
                valid_external_ids=[],
                new_external_ids=[],
                samples=[],
                enrichment=None,
                artifact_path=artifact_path,
            )

        raw_records = response.data if isinstance(response.data, list) else []
        deduplicated = _deduplicate_raw_records(raw_records)
        counters.records_seen = min(len(deduplicated), max(1, limit))
        for raw in deduplicated[: max(1, limit)]:
            match, reasons = normalize_trusted_ewc_map(raw)
            if match is None:
                counters.records_excluded += 1
                exclusion_reasons.update(reasons or ["invalid_ewc_map"])
                _append_sample(samples, raw, "excluded", reasons)
                continue

            classification, quality_reasons = classify_training_match(match, matcher)
            classifications.update([classification])
            if classification != "tier1":
                counters.records_excluded += 1
                exclusion_reasons.update(quality_reasons or ["not_strict_tier1"])
                _append_sample(samples, raw, classification, quality_reasons)
                continue

            existing = _find_existing_map(db, match.external_id)
            would_create += int(existing is None)
            would_update += int(existing is not None)
            valid_external_ids.append(match.external_id)
            if existing is None:
                new_external_ids.append(match.external_id)

            if apply:
                before: tuple[Any, ...] | None = None
                if existing is None:
                    db_match, created = upsert_match(db, match, matcher=matcher, enforce_tier1=True)
                    if db_match is None:
                        counters.records_excluded += 1
                        exclusion_reasons.update(["database_upsert_rejected"])
                        continue
                else:
                    db_match, created = existing, False
                    before = _metadata_state(db_match)
                    _fill_missing_result(db_match, match)
                _apply_training_metadata(db_match)
                counters.records_created += int(created)
                counters.records_updated += int(
                    not created and before is not None and before != _metadata_state(db_match)
                )
            _append_sample(samples, raw, classification, [])

        if apply:
            write_sync_log(
                db,
                source="opendota",
                sync_type="ewc_map_details",
                status="warning" if counters.records_excluded or errors else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(errors) if errors else None,
                metadata_json={
                    "league_id": EWC_LEAGUE_ID,
                    "classifications": dict(classifications),
                    "new_match_ids": new_external_ids,
                    "training_started": False,
                    "promotion_started": False,
                },
            )
            db.commit()
        else:
            db.rollback()
    except Exception as exc:
        db.rollback()
        errors.append(f"{exc.__class__.__name__}: {exc}")
    finally:
        db.close()

    if apply and valid_external_ids and not errors:
        try:
            enrichment = enricher(
                apply=True,
                limit=max(1, min(enrich_limit, len(valid_external_ids))),
                sleep_seconds=max(0.0, sleep_seconds),
                external_sources={"csv_import", "opendota", "stratz"},
                external_ids=valid_external_ids,
                client=client,
            )
            if enrichment.get("status") == "failed":
                errors.extend(enrichment.get("source_errors") or ["EWC detail enrichment failed."])
            elif enrichment.get("status") == "warning":
                warnings.append("Some EWC map details could not be enriched; the next scheduler cycle will retry them.")
        except Exception as exc:
            errors.append(f"EWC detail enrichment failed: {exc.__class__.__name__}: {exc}")

    return _finish(
        apply=apply,
        counters=counters,
        classifications=classifications,
        exclusion_reasons=exclusion_reasons,
        errors=errors,
        warnings=warnings,
        would_create=would_create,
        would_update=would_update,
        valid_external_ids=valid_external_ids,
        new_external_ids=new_external_ids,
        samples=samples,
        enrichment=enrichment,
        artifact_path=artifact_path,
    )


def normalize_trusted_ewc_map(raw: Any) -> tuple[NormalizedMatch | None, list[str]]:
    if not isinstance(raw, dict):
        return None, ["record_not_object"]
    match_id = str(raw.get("match_id") or "")
    league_id = str(raw.get("leagueid") or raw.get("league_id") or "")
    team_a_id = str(raw.get("radiant_team_id") or "")
    team_b_id = str(raw.get("dire_team_id") or "")
    team_a_name = resolve_source_team("opendota", team_a_id, raw.get("radiant_name"))
    team_b_name = resolve_source_team("opendota", team_b_id, raw.get("dire_name"))
    tournament_name = resolve_source_tournament("opendota", league_id, raw.get("league_name"))
    start_time = normalize_datetime(raw.get("start_time"))
    reasons: list[str] = []
    if league_id != EWC_LEAGUE_ID:
        reasons.append("untrusted_league_id")
    if not match_id.isdigit():
        reasons.append("invalid_match_id")
    if _positive_int(raw.get("duration")) <= 0:
        reasons.append("match_not_finished")
    if not isinstance(raw.get("radiant_win"), bool):
        reasons.append("winner_missing")
    if not team_a_id or not team_a_name:
        reasons.append("team_a_unmapped")
    if not team_b_id or not team_b_name:
        reasons.append("team_b_unmapped")
    if not tournament_name:
        reasons.append("tournament_unmapped")
    if start_time is None:
        reasons.append("start_time_missing")
    if reasons:
        return None, reasons
    return (
        NormalizedMatch(
            external_source="opendota",
            external_id=match_id,
            team_a_external_id=team_a_id,
            team_b_external_id=team_b_id,
            team_a_name=team_a_name,
            team_b_name=team_b_name,
            tournament_name=tournament_name,
            start_time=start_time,
            status="finished",
            winner_team_external_id=team_a_id if raw["radiant_win"] else team_b_id,
            raw_team_a=str(raw.get("radiant_name") or "") or None,
            raw_team_b=str(raw.get("dire_name") or "") or None,
            raw_team_a_id=team_a_id,
            raw_team_b_id=team_b_id,
            raw_tournament=str(raw.get("league_name") or "") or None,
            raw_tournament_id=league_id,
        ),
        [],
    )


def _find_existing_map(db, external_id: str) -> Match | None:
    source_order = case(
        (Match.external_source == "csv_import", 0),
        (Match.external_source == "opendota", 1),
        else_=2,
    )
    return db.scalar(
        select(Match)
        .options(selectinload(Match.team_a), selectinload(Match.team_b))
        .where(Match.external_id == external_id, Match.external_source.in_(MAP_SOURCES))
        .order_by(source_order, Match.id.asc())
        .limit(1)
    )


def _fill_missing_result(existing: Match, incoming: NormalizedMatch) -> None:
    existing.status = "finished"
    if existing.winner_team_id is not None:
        return
    winner_identity = canonical_team_identity_name(
        incoming.team_a_name
        if incoming.winner_team_external_id == incoming.team_a_external_id
        else incoming.team_b_name or ""
    )
    if canonical_team_identity_name(existing.team_a.name) == winner_identity:
        existing.winner_team_id = existing.team_a_id
    elif canonical_team_identity_name(existing.team_b.name) == winner_identity:
        existing.winner_team_id = existing.team_b_id


def _apply_training_metadata(match: Match) -> None:
    match.dataset_profile = "historical_training"
    match.competition_tier = "tier1"
    match.verification_status = "verified"
    match.source_confidence = "high"
    match.is_training_eligible = True
    match.is_prediction_eligible = False
    match.prediction_block_reason = "historical_match"
    match.prediction_guard_level = "normal"
    match.is_tier1_match = True
    match.excluded_reason = None


def _metadata_state(match: Match) -> tuple[Any, ...]:
    return (
        match.status,
        match.winner_team_id,
        match.dataset_profile,
        match.competition_tier,
        match.verification_status,
        match.source_confidence,
        match.is_training_eligible,
        match.is_prediction_eligible,
        match.prediction_block_reason,
        match.prediction_guard_level,
        match.is_tier1_match,
        match.excluded_reason,
    )


def _deduplicate_raw_records(records: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in records:
        if isinstance(raw, dict) and raw.get("match_id") is not None:
            by_id[str(raw["match_id"])] = raw
    return sorted(by_id.values(), key=lambda row: int(row.get("start_time") or 0), reverse=True)


def _append_sample(samples: list[dict[str, Any]], raw: dict[str, Any], classification: str, reasons: list[str]) -> None:
    if len(samples) >= 20:
        return
    samples.append(
        {
            "match_id": str(raw.get("match_id") or ""),
            "radiant_team_id": str(raw.get("radiant_team_id") or ""),
            "dire_team_id": str(raw.get("dire_team_id") or ""),
            "classification": classification,
            "reasons": reasons,
        }
    )


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _finish(
    *,
    apply: bool,
    counters: SyncCounters,
    classifications: Counter[str],
    exclusion_reasons: Counter[str],
    errors: list[str],
    warnings: list[str],
    would_create: int,
    would_update: int,
    valid_external_ids: list[str],
    new_external_ids: list[str],
    samples: list[dict[str, Any]],
    enrichment: dict[str, Any] | None,
    artifact_path: str | Path | None,
) -> dict[str, Any]:
    report = {
        "status": "failed" if errors else "warning" if warnings or counters.records_excluded else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if apply else "dry_run",
        "source": "opendota",
        "league_id": EWC_LEAGUE_ID,
        "records_seen": counters.records_seen,
        "valid_tier1_maps": len(valid_external_ids),
        "would_create": would_create if not apply else 0,
        "would_update": would_update if not apply else 0,
        "records_created": counters.records_created if apply else 0,
        "records_updated": counters.records_updated if apply else 0,
        "records_excluded": counters.records_excluded,
        "new_match_ids": new_external_ids,
        "classifications": dict(classifications),
        "exclusion_reasons": dict(exclusion_reasons),
        "detail_enrichment": _enrichment_summary(enrichment),
        "errors": errors,
        "warnings": warnings,
        "samples": samples,
        "apply_allowed": bool(valid_external_ids) and not errors,
        "training_started": False,
        "promotion_started": False,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def _enrichment_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        key: report.get(key)
        for key in (
            "status",
            "records_seen",
            "details_fetched",
            "matches_enriched",
            "records_excluded",
            "skipped_existing",
            "draft_entries_created",
            "draft_entries_updated",
            "source_errors",
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync verified finished EWC 2026 maps and OpenDota details.")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--enrich-limit", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync_ewc_map_details(
        apply=args.apply,
        limit=args.limit,
        enrich_limit=args.enrich_limit,
        sleep_seconds=args.sleep,
    )


if __name__ == "__main__":
    main()
