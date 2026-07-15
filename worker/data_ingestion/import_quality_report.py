from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.csv_import import _external_id, _row_to_match, validate_csv_row_metadata
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.normalizer import normalize_lookup_key, normalize_match_format


IMPORT_QUALITY_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "import_quality_report.json"
REQUIRED_FIELDS = {
    "team_a_name",
    "team_b_name",
    "tournament_name",
    "start_time",
    "format",
    "status",
}


def build_import_quality_report(
    path: str | Path,
    *,
    artifact_path: str | Path | None = IMPORT_QUALITY_REPORT_PATH,
) -> dict[str, Any]:
    rows = _read_rows(path)
    matcher = Tier1Matcher()
    warnings: list[str] = []
    errors: list[str] = []
    duplicate_keys: Counter[tuple] = Counter()
    valid_rows = 0
    excluded_rows = 0
    reasons: Counter[str] = Counter()

    for index, row in enumerate(rows, start=2):
        row_reasons = []
        missing = [field for field in REQUIRED_FIELDS if not (row.get(field) or "").strip()]
        row_reasons.extend([f"missing_{field}" for field in missing])
        match = _row_to_match(row)
        quality = validate_match(match, matcher=matcher)
        row_reasons.extend(quality.reasons)
        row_reasons.extend(validate_csv_row_metadata(row))
        if match.start_time is not None and (match.start_time.year < 2011 or match.start_time.year > 2035):
            row_reasons.append("impossible_date")
        if normalize_match_format(row.get("format")) not in {"BO1", "BO2", "BO3", "BO5", "unknown"}:
            row_reasons.append("invalid_format")
        duplicate_keys[_row_duplicate_key(row, match)] += 1
        if row_reasons:
            excluded_rows += 1
            reasons.update(row_reasons)
            errors.append(f"row={index}: {', '.join(row_reasons)}")
        else:
            valid_rows += 1

    for key, count in duplicate_keys.items():
        if count > 1:
            reasons.update(["duplicate_row"])
            warnings.append(f"duplicate row key={key} count={count}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if errors else "warning" if warnings else "ok",
        "file": str(path),
        "rows_seen": len(rows),
        "estimated_valid_rows": valid_rows,
        "estimated_excluded_rows": excluded_rows,
        "reason_counts": dict(reasons),
        "warnings": warnings,
        "errors": errors,
    }
    if artifact_path is not None:
        target = Path(artifact_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _row_duplicate_key(row: dict[str, str], match) -> tuple:
    series_id = (row.get("series_id") or "").strip()
    game_number = (row.get("game_number") or "").strip()
    if series_id and game_number:
        return ("series", series_id, game_number)
    external_id = _external_id(row)
    if external_id:
        return ("external", external_id)
    return (
        "tuple",
        normalize_lookup_key(match.team_a_name or ""),
        normalize_lookup_key(match.team_b_name or ""),
        normalize_lookup_key(match.tournament_name or ""),
        match.start_time.isoformat() if match.start_time else "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check CSV import quality before apply.")
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(build_import_quality_report(args.path), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
