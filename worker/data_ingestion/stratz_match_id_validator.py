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

from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.normalizer import (
    NormalizedMatch,
    normalize_lookup_key,
    normalize_stratz_matches,
    normalize_team_name,
    normalize_tournament_name,
)
from worker.data_ingestion.sources.stratz_client import StratzSourceClient


STRATZ_MATCH_ID_VALIDATION_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "stratz_match_id_validation_report.json"
REQUIRED_FIELDS = {
    "match_id",
    "expected_team_a_name",
    "expected_team_b_name",
    "expected_tournament_name",
    "expected_start_date",
    "source_url",
}


def validate_stratz_match_id_batch(
    path: str | Path,
    *,
    client: StratzSourceClient | None = None,
    artifact_path: str | Path | None = STRATZ_MATCH_ID_VALIDATION_REPORT_PATH,
    limit: int | None = None,
) -> dict[str, Any]:
    rows = _read_rows(path)
    if limit is not None:
        rows = rows[:limit]
    client = client or StratzSourceClient()
    matcher = Tier1Matcher()
    errors: list[str] = []
    warnings: list[str] = []
    mismatches: list[dict[str, Any]] = []
    valid_ids: list[str] = []
    invalid_ids: list[str] = []
    tier1_valid_count = 0
    seen_ids: Counter[str] = Counter()
    normalized_samples: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        match_id = (row.get("match_id") or "").strip()
        row_errors = _validate_required(row)
        if match_id:
            seen_ids.update([match_id])
        if not _valid_source_url(row.get("source_url")):
            row_errors.append("source_url_required")
        if seen_ids[match_id] > 1:
            row_errors.append("duplicate_match_id")
        if not match_id:
            row_errors.append("match_id_required")
        if row_errors:
            invalid_ids.append(match_id or f"row_{index}")
            errors.extend([f"row {index} match_id={match_id or 'missing'}: {error}" for error in row_errors])
            continue

        result = client.fetch_match_details(match_id)
        if not result.ok:
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: {result.error}")
            continue
        if not result.records:
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: STRATZ match not found")
            continue

        raw_match = result.records[0]
        normalized = normalize_stratz_matches([raw_match])
        if not normalized:
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: STRATZ details could not be normalized")
            continue
        match = normalized[0]
        normalized_samples.append(_match_sample(match, raw_match))

        row_mismatches = _expected_mismatches(row, match)
        if row_mismatches:
            invalid_ids.append(match_id)
            mismatches.extend({"match_id": match_id, **mismatch} for mismatch in row_mismatches)
            continue

        quality = validate_match(match, matcher=matcher)
        if quality.reasons:
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: {','.join(quality.reasons)}")
            continue
        if match.status != "finished":
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: match_not_finished")
            continue
        if not match.winner_team_external_id:
            invalid_ids.append(match_id)
            errors.append(f"match_id={match_id}: finished_match_missing_winner")
            continue

        valid_ids.append(match_id)
        if quality.is_tier1:
            tier1_valid_count += 1
        if not _draft_entries(raw_match):
            warnings.append(f"match_id={match_id}: draft fields missing")

    duplicate_ids = sorted(match_id for match_id, count in seen_ids.items() if match_id and count > 1)
    if duplicate_ids:
        warnings.append(f"duplicate match IDs detected: {', '.join(duplicate_ids[:10])}")

    safe_to_apply = not errors and not mismatches and tier1_valid_count > 0 and len(valid_ids) == len(rows)
    status = "ok" if safe_to_apply else ("failed" if errors or mismatches else "warning")
    report = {
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "file": str(path),
        "rows_seen": len(rows),
        "valid_match_ids": valid_ids,
        "invalid_match_ids": invalid_ids,
        "mismatched_expected_fields": mismatches,
        "tier1_valid_count": tier1_valid_count,
        "safe_to_apply": safe_to_apply,
        "errors": errors,
        "warnings": _unique(warnings),
        "normalized_samples": normalized_samples[:20],
    }
    _write_report(report, artifact_path)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _validate_required(row: dict[str, str]) -> list[str]:
    return [f"{field}_required" for field in sorted(REQUIRED_FIELDS) if not (row.get(field) or "").strip()]


def _valid_source_url(value: str | None) -> bool:
    text = (value or "").strip()
    return text.startswith(("http://", "https://"))


def _expected_mismatches(row: dict[str, str], match: NormalizedMatch) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    expected_a = normalize_team_name(row.get("expected_team_a_name"))
    expected_b = normalize_team_name(row.get("expected_team_b_name"))
    actual_a = match.team_a_name or ""
    actual_b = match.team_b_name or ""
    expected_pair = {normalize_lookup_key(expected_a), normalize_lookup_key(expected_b)}
    actual_pair = {normalize_lookup_key(actual_a), normalize_lookup_key(actual_b)}
    if expected_pair != actual_pair:
        mismatches.append(
            {
                "field": "expected_teams",
                "expected": f"{expected_a} vs {expected_b}",
                "actual": f"{actual_a} vs {actual_b}",
            }
        )
    expected_tournament = normalize_tournament_name(row.get("expected_tournament_name")) or ""
    if normalize_lookup_key(expected_tournament) != normalize_lookup_key(match.tournament_name or ""):
        mismatches.append(
            {
                "field": "expected_tournament_name",
                "expected": expected_tournament,
                "actual": match.tournament_name or "",
            }
        )
    expected_date = (row.get("expected_start_date") or "").strip()
    actual_date = match.start_time.date().isoformat() if match.start_time else ""
    if expected_date and actual_date != expected_date:
        mismatches.append({"field": "expected_start_date", "expected": expected_date, "actual": actual_date})
    return mismatches


def _match_sample(match: NormalizedMatch, raw_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": match.external_id,
        "team_a_name": match.team_a_name,
        "team_b_name": match.team_b_name,
        "team_a_id": match.team_a_external_id,
        "team_b_id": match.team_b_external_id,
        "tournament_name": match.tournament_name,
        "tournament_id": match.raw_tournament_id,
        "start_time": match.start_time.isoformat() if match.start_time else None,
        "status": match.status,
        "winner_team_id": match.winner_team_external_id,
        "draft_available": bool(_draft_entries(raw_match)),
        "patch_version": raw_match.get("gameVersion") or raw_match.get("gameVersionId") or raw_match.get("patch"),
        "duration": raw_match.get("durationSeconds") or raw_match.get("duration"),
    }


def _draft_entries(raw_match: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("pickBans", "picksBans", "draftEntries", "draft"):
        value = raw_match.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate manually verified STRATZ match ID batches.")
    parser.add_argument("path")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    validate_stratz_match_id_batch(args.path, limit=args.limit)


if __name__ == "__main__":
    main()
