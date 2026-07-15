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
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.normalizer import NormalizedMatch, normalize_lookup_key, normalize_pandascore_matches
from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


EWC_SYNC_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "ewc_sync_report.json"
DEFAULT_START_DATE = "2026-07-07"
DEFAULT_END_DATE = "2026-07-20"


def sync_ewc_matches(
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    limit: int = 300,
    apply: bool = False,
    artifact_path: str | Path | None = EWC_SYNC_REPORT_PATH,
) -> dict[str, Any]:
    db = get_session()
    matcher = Tier1Matcher()
    client = PandaScoreClient()
    counters = SyncCounters()
    source_errors: list[str] = []
    warnings: list[str] = []
    competition_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    would_create = 0
    would_update = 0
    started_at = datetime.now(UTC)

    try:
        if not client.enabled:
            source_errors.append("PANDASCORE_API_KEY is not configured.")
            report = _build_report(
                mode="apply" if apply else "dry_run",
                counters=counters,
                would_create=0,
                would_update=0,
                competition_counts=competition_counts,
                status_counts=status_counts,
                source_errors=source_errors,
                warnings=warnings,
                sample_matches=[],
                apply_allowed=False,
                apply_block_reason="PandaScore API key is missing.",
            )
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
            return report

        raw_records, fetch_errors = _fetch_ewc_raw_records(client, start_date=start_date, end_date=end_date, limit=limit)
        source_errors.extend(fetch_errors)
        normalized = normalize_pandascore_matches(raw_records)
        normalized = [match for match in normalized if _is_ewc_match(match)]
        normalized = _dedupe_matches(normalized)[:limit]
        counters.records_seen = len(normalized)

        sample_matches: list[dict[str, Any]] = []
        for match in normalized:
            competition_tier, prediction_block_reason = _classify_match(match, matcher)
            competition_counts.update([competition_tier])
            status_counts.update([match.status])
            _append_sample(sample_matches, match, competition_tier, prediction_block_reason)

            existing = _existing_match(db, match)
            if not apply:
                would_create += int(existing is None)
                would_update += int(existing is not None)
                continue

            db_match, was_created = upsert_match(db, match, matcher=matcher, enforce_tier1=False)
            if db_match is None:
                counters.records_excluded += 1
                continue
            _apply_ewc_metadata(
                db_match,
                match=match,
                competition_tier=competition_tier,
                prediction_block_reason=prediction_block_reason,
            )
            counters.records_created += int(was_created)
            counters.records_updated += int(not was_created)

        apply_allowed = counters.records_seen > 0 and not source_errors
        apply_block_reason = None if apply_allowed else "No EWC matches found or source errors occurred."
        if apply:
            write_sync_log(
                db,
                source="pandascore",
                sync_type="ewc_matches",
                status="warning" if source_errors else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors) if source_errors else None,
                metadata_json={
                    "start_date": start_date,
                    "end_date": end_date,
                    "competition_counts": dict(competition_counts),
                    "status_counts": dict(status_counts),
                    "training_eligible": False,
                },
            )
            db.commit()
        else:
            db.rollback()

        report = _build_report(
            mode="apply" if apply else "dry_run",
            counters=counters,
            would_create=would_create,
            would_update=would_update,
            competition_counts=competition_counts,
            status_counts=status_counts,
            source_errors=source_errors,
            warnings=warnings,
            sample_matches=sample_matches,
            apply_allowed=apply_allowed,
            apply_block_reason=apply_block_reason,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def _fetch_ewc_raw_records(
    client: PandaScoreClient,
    *,
    start_date: str,
    end_date: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    page = 1
    while len(records) < limit:
        response = client.get_matches(start_date=start_date, end_date=end_date, limit=100, page=page)
        if not response.ok:
            errors.append(response.error or "PandaScore past matches fetch failed.")
            break
        page_records = response.data if isinstance(response.data, list) else []
        records.extend(_ewc_raw_only(page_records))
        if len(page_records) < 100:
            break
        page += 1

    for label, response in (
        ("running", client.get_running_matches(limit=100)),
        ("upcoming", client.get_upcoming_matches(limit=100, from_date=start_date, to_date=end_date)),
    ):
        if not response.ok:
            errors.append(f"{label}: {response.error}")
            continue
        records.extend(_ewc_raw_only(response.data if isinstance(response.data, list) else []))
    return records, errors


def _ewc_raw_only(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if isinstance(record, dict) and _raw_is_ewc(record)]


def _raw_is_ewc(record: dict[str, Any]) -> bool:
    league = record.get("league") if isinstance(record.get("league"), dict) else {}
    serie = record.get("serie") if isinstance(record.get("serie"), dict) else {}
    tournament = record.get("tournament") if isinstance(record.get("tournament"), dict) else {}
    text = " ".join(
        str(value)
        for value in (
            league.get("name"),
            league.get("slug"),
            serie.get("full_name"),
            serie.get("slug"),
            tournament.get("name"),
            tournament.get("slug"),
        )
        if value
    )
    return "esports world cup" in normalize_lookup_key(text)


def _is_ewc_match(match: NormalizedMatch) -> bool:
    return normalize_lookup_key(match.tournament_name or "") == "esports world cup"


def _dedupe_matches(matches: list[NormalizedMatch]) -> list[NormalizedMatch]:
    by_id: dict[str, NormalizedMatch] = {}
    for match in matches:
        if match.external_id:
            by_id[match.external_id] = match
    return sorted(by_id.values(), key=lambda item: item.start_time or datetime.min.replace(tzinfo=UTC))


def _existing_match(db, match: NormalizedMatch) -> Match | None:
    return db.scalar(select(Match).where(Match.external_source == match.external_source, Match.external_id == match.external_id))


def _classify_match(match: NormalizedMatch, matcher: Tier1Matcher) -> tuple[str, str | None]:
    reasons: list[str] = []
    team_a_tier1 = matcher.is_tier1_team(match.team_a_name or "")
    team_b_tier1 = matcher.is_tier1_team(match.team_b_name or "")
    tournament_tier1 = matcher.is_tier1_tournament(match.tournament_name or "")
    if not team_a_tier1:
        reasons.append("team_a_not_active_tier1")
    if not team_b_tier1:
        reasons.append("team_b_not_active_tier1")
    if not tournament_tier1:
        reasons.append("tournament_not_tier1_allowlist")
    if match.status == "finished":
        reasons.append("match_already_finished")
    if team_a_tier1 and team_b_tier1 and tournament_tier1:
        return "tier1", "match_already_finished" if match.status == "finished" else None
    return "pro", ",".join(reasons) if reasons else None


def _apply_ewc_metadata(
    db_match: Match,
    *,
    match: NormalizedMatch,
    competition_tier: str,
    prediction_block_reason: str | None,
) -> None:
    db_match.dataset_profile = "ewc_2026"
    db_match.competition_tier = competition_tier
    db_match.verification_status = "verified"
    db_match.source_confidence = "high"
    db_match.is_training_eligible = False
    db_match.is_prediction_eligible = bool(match.status in {"upcoming", "live"} and competition_tier == "tier1")
    db_match.prediction_block_reason = None if db_match.is_prediction_eligible else prediction_block_reason
    db_match.prediction_guard_level = "normal" if db_match.is_prediction_eligible else "high"


def _append_sample(
    samples: list[dict[str, Any]],
    match: NormalizedMatch,
    competition_tier: str,
    prediction_block_reason: str | None,
) -> None:
    if len(samples) >= 20:
        return
    samples.append(
        {
            "external_id": match.external_id,
            "team_a": match.team_a_name,
            "team_b": match.team_b_name,
            "tournament": match.tournament_name,
            "start_time": match.start_time.isoformat() if match.start_time else None,
            "status": match.status,
            "format": match.format,
            "is_draw": match.is_draw,
            "competition_tier": competition_tier,
            "prediction_block_reason": prediction_block_reason,
        }
    )


def _build_report(
    *,
    mode: str,
    counters: SyncCounters,
    would_create: int,
    would_update: int,
    competition_counts: Counter[str],
    status_counts: Counter[str],
    source_errors: list[str],
    warnings: list[str],
    sample_matches: list[dict[str, Any]],
    apply_allowed: bool,
    apply_block_reason: str | None,
) -> dict[str, Any]:
    return {
        "status": "warning" if source_errors else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "source": "pandascore",
        "scope": "esports_world_cup_dota2",
        "records_seen": counters.records_seen,
        "would_create": would_create if mode == "dry_run" else 0,
        "would_update": would_update if mode == "dry_run" else 0,
        "records_created": counters.records_created if mode == "apply" else 0,
        "records_updated": counters.records_updated if mode == "apply" else 0,
        "records_excluded": counters.records_excluded,
        "competition_counts": dict(competition_counts),
        "status_counts": dict(status_counts),
        "source_errors": source_errors,
        "warnings": warnings,
        "sample_matches": sample_matches,
        "apply_allowed": apply_allowed,
        "apply_block_reason": apply_block_reason,
        "training_eligible": False,
        "recommendation": "review_report_then_apply" if mode == "dry_run" and apply_allowed else "synced_for_analysis",
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
    parser = argparse.ArgumentParser(description="Sync Esports World Cup Dota 2 matches from PandaScore.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync_ewc_matches(
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
