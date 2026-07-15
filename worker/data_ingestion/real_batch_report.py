from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.config import ML_ARTIFACT_DIR


REAL_BATCH_PIPELINE_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "real_batch_pipeline_report.json"
PATHS = {
    "validation": Path(ML_ARTIFACT_DIR) / "real_batch_validation_report.json",
    "import_quality": Path(ML_ARTIFACT_DIR) / "import_quality_report.json",
    "csv_import": Path(ML_ARTIFACT_DIR) / "csv_import_report.json",
    "coverage": Path(ML_ARTIFACT_DIR) / "data_coverage_report.json",
    "audit": Path(ML_ARTIFACT_DIR) / "project_audit_report.json",
    "match_validation": Path(ML_ARTIFACT_DIR) / "match_validation_report.json",
    "training": Path(ML_ARTIFACT_DIR) / "training_report.json",
    "backtest": Path(ML_ARTIFACT_DIR) / "backtest_report.json",
}


def build_real_batch_report(*, artifact_path: str | Path | None = REAL_BATCH_PIPELINE_REPORT_PATH) -> dict[str, Any]:
    validation = _read(PATHS.get("validation"))
    import_quality = _read(PATHS.get("import_quality"))
    csv_import = _read(PATHS.get("csv_import"))
    coverage = _read(PATHS.get("coverage"))
    audit = _read(PATHS.get("audit"))
    match_validation = _read(PATHS.get("match_validation"))
    training = _read(PATHS.get("training"))
    backtest = _read(PATHS.get("backtest"))

    real_after = _real_count_from_coverage(coverage)
    imported_rows = _imported_rows(csv_import)
    would_import_rows = _would_import_rows(csv_import, import_quality)
    batch_applied = csv_import.get("mode") == "apply" and csv_import.get("status") != "failed"
    warnings = []
    errors = []
    for payload in (validation, import_quality, csv_import, audit, match_validation):
        warnings.extend(payload.get("warnings") or [])
        errors.extend(payload.get("errors") or [])
    if coverage.get("dev_seed_only"):
        warnings.append("Current coverage is dev_seed_only; no real rows are available for real accuracy.")
    if real_after < 300:
        warnings.append("Fewer than 300 real Tier 1 historical matches; do not trust real model metrics yet.")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if errors else "warning" if warnings else "ok",
        "real_matches_before": max(0, real_after - imported_rows) if csv_import.get("mode") == "apply" else real_after,
        "real_matches_after": real_after,
        "imported_rows": imported_rows,
        "would_import_rows": would_import_rows,
        "excluded_rows": import_quality.get("estimated_excluded_rows", 0),
        "coverage_readiness": coverage.get("training_readiness"),
        "dev_seed_only": coverage.get("dev_seed_only"),
        "dataset_type": _dataset_type(backtest, coverage),
        "candidate_created": batch_applied and bool(training.get("model_version_id")),
        "candidate_version": (
            training.get("version") or str(training.get("model_version_id"))
            if batch_applied and training.get("model_version_id")
            else None
        ),
        "backtest_status": backtest.get("status", "available" if backtest else "missing") if batch_applied else "not_run",
        "best_by_log_loss": backtest.get("best_by_log_loss") if batch_applied else None,
        "best_by_brier_score": backtest.get("best_by_brier_score") if batch_applied else None,
        "warnings": warnings,
        "errors": errors,
        "recommended_next_step": _recommended_next_step(errors, real_after, batch_applied, would_import_rows),
    }
    if artifact_path is not None:
        target = Path(artifact_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report


def _read(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "failed", "errors": [f"{path} is unreadable"]}


def _real_count_from_coverage(coverage: dict[str, Any]) -> int:
    sources = coverage.get("matches_by_source") or {}
    return sum(count for source, count in sources.items() if source != "dev_seed")


def _imported_rows(csv_import: dict[str, Any]) -> int:
    if csv_import.get("mode") != "apply" or csv_import.get("status") == "failed":
        return 0
    return int(csv_import.get("created") or 0)


def _would_import_rows(csv_import: dict[str, Any], import_quality: dict[str, Any]) -> int:
    if csv_import.get("mode") == "dry_run" and csv_import.get("status") != "failed":
        return int(csv_import.get("would_create") or 0)
    return int(import_quality.get("estimated_valid_rows") or 0)


def _dataset_type(backtest: dict[str, Any], coverage: dict[str, Any]) -> str:
    if backtest.get("dataset_type"):
        return backtest["dataset_type"]
    if coverage.get("dev_seed_only"):
        return "dev_seed"
    return "unknown"


def _recommended_next_step(errors: list[str], real_rows: int, batch_applied: bool, would_import_rows: int) -> str:
    if errors:
        return "Fix validation errors before import."
    if not batch_applied:
        if would_import_rows <= 0:
            return "No valid new rows found; do not apply this batch."
        return "Review the dry-run and apply only a manually verified real batch; training and promotion remain separate."
    if real_rows < 300:
        return "Collect more real Tier 1 historical rows before trusting metrics."
    return "Review candidate and backtest manually; do not auto-promote."


def main() -> None:
    parser = argparse.ArgumentParser(description="Build latest real batch pipeline report.")
    parser.parse_args()
    print(json.dumps(build_real_batch_report(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
