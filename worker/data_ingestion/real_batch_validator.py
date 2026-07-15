from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
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
from sqlalchemy.orm import Session

from app.db.models import Match
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.csv_import import _external_id, _row_to_match, validate_csv_row_metadata
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.db import get_session
from worker.data_ingestion.import_quality_report import build_import_quality_report
from worker.data_ingestion.normalizer import normalize_lookup_key


REAL_BATCH_VALIDATION_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "real_batch_validation_report.json"


def validate_real_batch(
    path: str | Path,
    db: Session,
    *,
    artifact_path: str | Path | None = REAL_BATCH_VALIDATION_REPORT_PATH,
) -> dict[str, Any]:
    path = Path(path)
    quality = build_import_quality_report(path, artifact_path=None)
    rows = _read_rows(path)
    errors: list[str] = list(quality.get("errors", []))
    warnings: list[str] = list(quality.get("warnings", []))
    duplicate_rows = int((quality.get("reason_counts") or {}).get("duplicate_row", 0))
    suspected_existing_duplicates = 0
    valid_rows = 0
    excluded_rows = 0
    missing_source_url = 0

    if "dev_seed" in path.name.lower():
        errors.append("filename_contains_dev_seed")

    for index, row in enumerate(rows, start=2):
        row_reasons = []
        if (row.get("external_source") or "").strip() == "dev_seed":
            row_reasons.append("external_source_dev_seed_rejected")
        match = _row_to_match(row)
        row_reasons.extend(validate_match(match).reasons)
        row_reasons.extend(validate_csv_row_metadata(row))
        if not (row.get("source_url") or "").strip():
            missing_source_url += 1
        if _has_existing_duplicate(db, match):
            suspected_existing_duplicates += 1
            warnings.append(f"row={index}: possible_existing_duplicate")
        if row_reasons:
            excluded_rows += 1
            errors.append(f"row={index}: {', '.join(row_reasons)}")
        else:
            valid_rows += 1

    if rows and missing_source_url / len(rows) >= 0.5:
        warnings.append("source_url missing for many rows; review provenance before apply.")
    if valid_rows == 0:
        errors.append("real batch has 0 valid rows")

    status = "failed" if errors else "warning" if warnings else "ok"
    recommendation = "fix_errors_before_import" if status == "failed" else "safe_to_apply_after_review" if status == "ok" else "safe_to_dry_run"
    report = {
        "status": status,
        "file": str(path),
        "rows_seen": len(rows),
        "valid_rows": valid_rows,
        "excluded_rows": excluded_rows,
        "duplicate_rows": duplicate_rows,
        "suspected_existing_duplicates": suspected_existing_duplicates,
        "errors": errors,
        "warnings": warnings,
        "recommendation": recommendation,
        "import_quality_status": quality.get("status"),
    }
    if artifact_path is not None:
        target = Path(artifact_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _has_existing_duplicate(db: Session, match) -> bool:
    external_id = _external_id(
        {
            "external_id": match.external_id,
            "series_id": "",
            "game_number": "",
        }
    )
    if external_id:
        existing = db.scalar(select(Match.id).where(Match.external_source == "csv_import", Match.external_id == external_id))
        if existing:
            return True
    if not match.start_time or not match.tournament_name:
        return False
    candidates = db.scalars(
        select(Match).where(
            Match.start_time >= match.start_time,
            Match.start_time <= match.start_time,
        )
    ).all()
    incoming_teams = {
        normalize_lookup_key(match.team_a_name or ""),
        normalize_lookup_key(match.team_b_name or ""),
    }
    for candidate in candidates:
        if normalize_lookup_key(candidate.tournament_name or "") != normalize_lookup_key(match.tournament_name or ""):
            continue
        candidate_teams = {
            normalize_lookup_key(candidate.team_a.name if candidate.team_a else ""),
            normalize_lookup_key(candidate.team_b.name if candidate.team_b else ""),
        }
        if candidate_teams == incoming_teams:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a real Tier 1 CSV batch before import.")
    parser.add_argument("path")
    args = parser.parse_args()
    db = get_session()
    try:
        print(json.dumps(validate_real_batch(args.path, db), indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
