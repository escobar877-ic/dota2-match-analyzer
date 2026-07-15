from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from sqlalchemy import select

from app.db.models import Match
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.normalizer import (
    NormalizedMatch,
    normalize_opendota_matches,
    normalize_stratz_matches,
    normalize_team_name,
    normalize_tournament_name,
)
from worker.data_ingestion.pro_match_quality import validate_verified_pro_match
from worker.data_ingestion.sources.opendota_client import OpenDotaSourceClient
from worker.data_ingestion.sources.stratz_client import StratzSourceClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "stratz_ids_import_report.json"
TRUSTED_OPENDOTA_LEAGUE_IDS = {
    "18324", "16935", "15728", "16881", "15475", "18375", "15439", "16201",
    "17765", "18111", "18988", "17509", "17795", "18058", "18358", "19543",
    "17414", "19101",
}


def read_match_ids(path: str | Path) -> list[str]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    if "," in lines[0] or lines[0].lower() == "match_id":
        rows = csv.DictReader(lines)
        values = [(row.get("match_id") or "").strip() for row in rows]
    else:
        values = [line.split(",", 1)[0].strip() for line in lines]
    return [value for value in values if value and value.lower() != "match_id"]


def read_batch_metadata(path: str | Path) -> dict[str, dict[str, str]]:
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "match_id" not in reader.fieldnames:
            return {}
        return {
            (row.get("match_id") or "").strip(): row
            for row in reader
            if (row.get("match_id") or "").strip()
        }


def import_stratz_ids(
    path: str | Path,
    *,
    apply: bool = False,
    limit: int | None = None,
    client: StratzSourceClient | None = None,
    opendota_client: OpenDotaSourceClient | None = None,
    artifact_path: str | Path | None = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    ids = read_match_ids(path)
    if limit is not None:
        ids = ids[:limit]
    duplicates = sorted(match_id for match_id, count in Counter(ids).items() if count > 1)
    unique_ids = list(dict.fromkeys(ids))
    client = client or StratzSourceClient()
    opendota_client = opendota_client or OpenDotaSourceClient()
    batch_metadata = read_batch_metadata(path)
    matcher = Tier1Matcher()
    db = get_session()
    counters = SyncCounters(records_seen=len(ids))
    classifications: Counter[str] = Counter()
    errors: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, Any]] = []
    would_create = 0
    would_update = 0
    detail_sources: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    excluded_samples: list[dict[str, Any]] = []

    if duplicates:
        warnings.append(f"Duplicate match IDs ignored: {', '.join(duplicates[:10])}")

    try:
        for match_id in unique_ids:
            if not match_id.isdigit():
                counters.records_excluded += 1
                classifications.update(["invalid"])
                errors.append(f"match_id={match_id}: numeric Dota match ID required")
                continue

            batch_row = batch_metadata.get(match_id)
            trusted_csv_match = normalized_match_from_trusted_league_csv(batch_row)
            if trusted_csv_match is not None:
                match = trusted_csv_match
                detail_source = "opendota_league_csv"
                detail_sources.update([detail_source])
            elif batch_row:
                result = opendota_client.fetch_match_details(match_id)
                detail_source = "opendota"
                normalizer = normalize_opendota_matches
            else:
                result = client.fetch_match_details(match_id)
                detail_source = "stratz"
                normalizer = normalize_stratz_matches
                if not result.ok or not result.records:
                    result = opendota_client.fetch_match_details(match_id)
                    detail_source = "opendota"
                    normalizer = normalize_opendota_matches
            if trusted_csv_match is None:
                if not result.ok or not result.records:
                    counters.records_excluded += 1
                    classifications.update(["invalid"])
                    errors.append(f"match_id={match_id}: {result.error or 'match details not found'}")
                    continue

                raw_match = result.records[0]
                normalized = normalizer([raw_match])
                if not normalized:
                    counters.records_excluded += 1
                    classifications.update(["invalid"])
                    errors.append(f"match_id={match_id}: response normalization failed")
                    continue
                match = normalized[0]
                detail_sources.update([detail_source])
                metadata_errors = validate_batch_metadata(batch_row, raw_match, detail_source)
                if metadata_errors:
                    counters.records_excluded += 1
                    classifications.update(["invalid"])
                    errors.extend(f"match_id={match_id}: {reason}" for reason in metadata_errors)
                    continue
                if batch_row:
                    match = replace(match, external_source="csv_import")
            classification, reasons = classify_training_match(match, matcher)
            classifications.update([classification])
            samples.append(
                {
                    "match_id": match_id,
                    "team_a": match.team_a_name,
                    "team_b": match.team_b_name,
                    "tournament": match.tournament_name,
                    "start_time": match.start_time.isoformat() if match.start_time else None,
                    "classification": classification,
                    "reasons": reasons,
                }
            )
            if classification == "excluded":
                counters.records_excluded += 1
                exclusion_reasons.update(reasons or ["excluded"])
                if len(excluded_samples) < 50:
                    excluded_samples.append(
                        {
                            "match_id": match_id,
                            "team_a": match.team_a_name,
                            "team_b": match.team_b_name,
                            "tournament": match.tournament_name,
                            "reasons": reasons,
                        }
                    )
                continue

            existing = db.scalar(
                select(Match).where(
                    Match.external_source == match.external_source,
                    Match.external_id == match_id,
                )
            )
            would_update += int(existing is not None)
            would_create += int(existing is None)
            if not apply:
                continue

            db_match, created = upsert_match(
                db,
                match,
                matcher=matcher,
                quality_scope="verified_pro",
                enforce_tier1=False,
            )
            if db_match is None:
                counters.records_excluded += 1
                errors.append(f"match_id={match_id}: database upsert rejected")
                continue
            db_match.dataset_profile = "historical_training"
            db_match.competition_tier = classification
            db_match.verification_status = "verified"
            db_match.source_confidence = "high"
            db_match.is_training_eligible = True
            db_match.is_prediction_eligible = classification == "tier1"
            db_match.prediction_block_reason = None if classification == "tier1" else "verified_pro_training_only"
            db_match.is_tier1_match = classification == "tier1"
            db_match.excluded_reason = None if classification == "tier1" else "verified_pro_training_only"
            counters.records_created += int(created)
            counters.records_updated += int(not created)

        valid_count = classifications["tier1"] + classifications["pro"]
        if apply:
            status = "ok" if valid_count and not errors else "warning"
            write_sync_log(
                db,
                source="match_id_batch",
                sync_type="stratz_ids_import",
                status=status,
                started_at=started_at,
                counters=counters,
                error_message="; ".join(errors) if errors else None,
                metadata_json={
                    "file": str(path),
                    "classifications": dict(classifications),
                    "training_profile": "tier1_plus_verified_pro",
                    "detail_sources": dict(detail_sources),
                },
            )
            db.commit()
        else:
            db.rollback()

        report = {
            "status": "ok" if valid_count else "warning",
            "mode": "apply" if apply else "dry_run",
            "generated_at": datetime.now(UTC).isoformat(),
            "file": str(path),
            "records_seen": len(ids),
            "unique_match_ids": len(unique_ids),
            "tier1_count": classifications["tier1"],
            "verified_pro_count": classifications["pro"],
            "excluded_count": counters.records_excluded,
            "would_create": would_create,
            "would_update": would_update,
            "records_created": counters.records_created,
            "records_updated": counters.records_updated,
            "safe_to_apply": valid_count > 0,
            "training_profile": "tier1_plus_verified_pro",
            "detail_sources": dict(detail_sources),
            "exclusion_reasons": dict(exclusion_reasons),
            "excluded_samples": excluded_samples,
            "errors": errors,
            "warnings": warnings,
            "samples": samples[:50],
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return report
    except Exception as exc:
        db.rollback()
        report = {
            "status": "failed",
            "mode": "apply" if apply else "dry_run",
            "records_seen": len(ids),
            "safe_to_apply": False,
            "errors": [str(exc)],
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return report
    finally:
        db.close()


def normalized_match_from_trusted_league_csv(
    row: dict[str, str] | None,
) -> NormalizedMatch | None:
    if not row or (row.get("source") or "").strip().lower() != "opendota":
        return None
    match_id = (row.get("match_id") or "").strip()
    league_id = (row.get("league_id") or "").strip()
    team_a_id = (row.get("radiant_team_id") or "").strip()
    team_b_id = (row.get("dire_team_id") or "").strip()
    team_a_name = normalize_team_name(row.get("radiant_name"))
    team_b_name = normalize_team_name(row.get("dire_name"))
    source_url = (row.get("opendota_match_url") or "").strip()
    duration = _positive_int(row.get("duration_sec"))
    start_timestamp = _positive_int(row.get("start_time_unix"))
    radiant_win = (row.get("radiant_win") or "").strip().lower()
    winner_side = (row.get("winner_side") or "").strip().lower()
    if (
        not match_id.isdigit()
        or league_id not in TRUSTED_OPENDOTA_LEAGUE_IDS
        or not team_a_id
        or not team_b_id
        or not team_a_name
        or not team_b_name
        or duration <= 0
        or start_timestamp <= 0
        or source_url != f"https://www.opendota.com/matches/{match_id}"
        or radiant_win not in {"true", "false"}
        or winner_side not in {"radiant", "dire"}
        or (radiant_win == "true") != (winner_side == "radiant")
    ):
        return None
    tournament_name = _canonical_tournament_name(
        (row.get("tournament_key") or "").strip(),
        row.get("tournament_name"),
    )
    if not tournament_name:
        return None
    return NormalizedMatch(
        external_source="csv_import",
        external_id=match_id,
        team_a_external_id=team_a_id,
        team_b_external_id=team_b_id,
        team_a_name=team_a_name,
        team_b_name=team_b_name,
        tournament_name=tournament_name,
        start_time=datetime.fromtimestamp(start_timestamp, tz=UTC),
        status="finished",
        winner_team_external_id=team_a_id if radiant_win == "true" else team_b_id,
        raw_team_a=(row.get("radiant_name") or "").strip(),
        raw_team_b=(row.get("dire_name") or "").strip(),
        raw_team_a_id=team_a_id,
        raw_team_b_id=team_b_id,
        raw_tournament=(row.get("tournament_name") or "").strip(),
        raw_tournament_id=league_id,
    )


def _canonical_tournament_name(key: str, value: str | None) -> str | None:
    if key.startswith("ti"):
        return "The International"
    if key.startswith("riyadh"):
        return "Riyadh Masters"
    if key.startswith("ewc"):
        return "Esports World Cup"
    if key.startswith("dreamleague"):
        return "DreamLeague"
    if key.startswith("esl_one"):
        return "ESL One"
    if key.startswith("pgl_wallachia"):
        return "PGL Wallachia"
    if key.startswith("blast_slam"):
        return "BLAST Slam"
    return normalize_tournament_name(value)


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def validate_batch_metadata(
    expected: dict[str, str] | None,
    raw: dict[str, Any],
    source: str,
) -> list[str]:
    if not expected or source != "opendota":
        return []
    errors: list[str] = []
    comparisons = {
        "league_id_mismatch": (expected.get("league_id"), raw.get("leagueid") or raw.get("league_id")),
        "radiant_team_id_mismatch": (expected.get("radiant_team_id"), raw.get("radiant_team_id")),
        "dire_team_id_mismatch": (expected.get("dire_team_id"), raw.get("dire_team_id")),
        "start_time_mismatch": (expected.get("start_time_unix"), raw.get("start_time")),
        "duration_mismatch": (expected.get("duration_sec"), raw.get("duration")),
    }
    for reason, (expected_value, actual_value) in comparisons.items():
        if expected_value and str(expected_value).strip() != str(actual_value):
            errors.append(reason)
    expected_radiant_win = (expected.get("radiant_win") or "").strip().lower()
    if expected_radiant_win:
        actual_radiant_win = str(bool(raw.get("radiant_win"))).lower()
        if expected_radiant_win != actual_radiant_win:
            errors.append("winner_mismatch")
    return errors


def classify_training_match(match, matcher: Tier1Matcher | None = None) -> tuple[str, list[str]]:
    matcher = matcher or Tier1Matcher()
    if match.status != "finished":
        return "excluded", ["match_not_finished"]
    if not match.winner_team_external_id:
        return "excluded", ["finished_match_missing_winner"]

    tier1_quality = validate_match(match, matcher=matcher)
    if tier1_quality.is_tier1 and not tier1_quality.reasons:
        return "tier1", []

    pro_quality = validate_verified_pro_match(match)
    if pro_quality.valid:
        return "pro", []
    return "excluded", list(dict.fromkeys([*tier1_quality.reasons, *pro_quality.reasons]))


def _write_report(report: dict[str, Any], path: str | Path | None) -> None:
    if path is None:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f"{target.suffix}.tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or import a plain list of STRATZ/Dota match IDs.")
    parser.add_argument("path", help="Text file or CSV containing a match_id column.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true", help="Write verified matches. Default is dry-run.")
    args = parser.parse_args()
    import_stratz_ids(args.path, apply=args.apply, limit=args.limit)


if __name__ == "__main__":
    main()
