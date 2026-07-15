from __future__ import annotations

import json
import os
import pickle
import sys
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
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

from app.database import SessionLocal
from app.db.models import Match, ModelVersion
from ml.config import ML_ARTIFACT_DIR
from ml.evaluation.metrics import calculate_classification_metrics
from ml.features.feature_schema import FEATURE_VERSION
from ml.models.calibration import calibrate_probabilities_guarded
from ml.models.elo_baseline import EloBaselineModel
from ml.models.logistic_regression_model import create_logistic_regression_model
from ml.models.random_forest_model import create_random_forest_model
from ml.safety import assert_local_ml_only, assert_no_forbidden_packages
from ml.training.config import DEFAULT_MODEL_VERSION_PREFIX, MIN_TRAINING_MATCHES
from ml.training.dataset_builder import NotEnoughTrainingDataError, build_training_dataset, split_time_based


MODEL_PATH = Path(ML_ARTIFACT_DIR) / "prematch_model.pkl"
CALIBRATOR_PATH = Path(ML_ARTIFACT_DIR) / "calibrator.pkl"
FEATURE_SCHEMA_PATH = Path(ML_ARTIFACT_DIR) / "feature_schema.json"
TRAINING_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "training_report.json"


def train_prematch_model(
    *,
    promote_if_better: bool = False,
    dev_allow_synthetic_promotion: bool = False,
    force_promote: bool = False,
    training_profile: str = "tier1_only",
    feature_set: str = "all",
) -> dict[str, Any]:
    assert_local_ml_only()
    assert_no_forbidden_packages()

    db = SessionLocal()
    try:
        dataset = build_training_dataset(
            db,
            min_rows=MIN_TRAINING_MATCHES,
            training_profile=training_profile,
            feature_set=feature_set,
        )
        split = split_time_based(dataset)

        candidates = [
            (
                "elo_baseline",
                "baseline",
                EloBaselineModel(
                    dataset.feature_schema.feature_names.index("elo_diff")
                    if "elo_diff" in dataset.feature_schema.feature_names
                    else 0
                ),
            ),
            ("logistic_regression", "sklearn", create_logistic_regression_model()),
            ("random_forest", "sklearn", create_random_forest_model()),
        ]

        results = []
        for model_name, model_type, model in candidates:
            if len(set(split.train.y)) < 2 and model_name != "elo_baseline":
                continue
            if model_name == "elo_baseline":
                model.fit(split.train.x, split.train.y)
            elif hasattr(model, "named_steps"):
                model.fit(
                    split.train.x,
                    split.train.y,
                    classifier__sample_weight=split.train.sample_weights,
                )
            else:
                model.fit(split.train.x, split.train.y, sample_weight=split.train.sample_weights)
            validation_probabilities = _positive_class_probabilities(model, split.validation.x)
            validation_metrics = calculate_classification_metrics(split.validation.y, validation_probabilities)
            tier1_validation_metrics = _subset_metrics(
                split.validation.y,
                validation_probabilities,
                split.validation.sample_weights,
            )
            results.append(
                {
                    "model_name": model_name,
                    "model_type": model_type,
                    "model": model,
                    "validation_probabilities": validation_probabilities,
                    "validation_metrics": validation_metrics,
                    "tier1_validation_metrics": tier1_validation_metrics,
                }
            )

        if not results:
            raise NotEnoughTrainingDataError("Not enough Tier 1 historical matches to train ML model.")

        selected = min(
            results,
            key=lambda item: (
                _metric_sort_value(
                    (item["tier1_validation_metrics"] or item["validation_metrics"]).get("log_loss")
                ),
                _metric_sort_value(
                    (item["tier1_validation_metrics"] or item["validation_metrics"]).get("brier_score")
                ),
            ),
        )
        calibrator, calibration_guard = calibrate_probabilities_guarded(
            selected["validation_probabilities"],
            split.validation.y,
        )
        test_probabilities = _positive_class_probabilities(selected["model"], split.test.x)
        if calibrator is not None:
            test_probabilities = calibrator.predict_proba(test_probabilities)
        test_metrics = calculate_classification_metrics(split.test.y, test_probabilities)
        tier1_test_metrics = _subset_metrics(split.test.y, test_probabilities, split.test.sample_weights)

        now = datetime.now(timezone.utc)
        version = f"{DEFAULT_MODEL_VERSION_PREFIX}_{now.strftime('%Y%m%d%H%M%S')}"
        source_types = _source_types(db, [row.match_id for row in dataset.rows])
        source_counts = _source_counts(db, [row.match_id for row in dataset.rows])
        dataset_type = _dataset_type(source_types)
        dataset_metadata = dict(dataset.metadata)
        dataset_metadata["feature_version"] = FEATURE_VERSION
        dataset_metadata["source_types"] = sorted(source_types)
        dataset_metadata["source_counts"] = source_counts
        dataset_metadata["dataset_type"] = dataset_type
        dataset_metadata["real_rows_count"] = sum(count for source, count in source_counts.items() if source != "dev_seed")
        dataset_metadata["dev_seed_rows_count"] = source_counts.get("dev_seed", 0)
        dataset_metadata["total_rows"] = len(dataset.rows)
        dataset_metadata["warnings"] = _dataset_warnings(dataset_metadata)
        dataset_metadata["tier1_rows_count"] = sum(
            1 for row in dataset.rows if row.sample_weight == 1.0
        )
        dataset_metadata["verified_pro_rows_count"] = sum(
            1 for row in dataset.rows if row.sample_weight < 1.0
        )
        if source_types == {"dev_seed"}:
            dataset_metadata["source"] = "dev_seed"

        report = {
            "feature_version": FEATURE_VERSION,
            "selected_model": selected["model_name"],
            "selected_model_type": selected["model_type"],
            "dataset_metadata": dataset_metadata,
            "rows": len(dataset.rows),
            "train_rows": len(split.train.rows),
            "validation_rows": len(split.validation.rows),
            "test_rows": len(split.test.rows),
            "candidate_metrics": {
                item["model_name"]: item["validation_metrics"]
                for item in results
            },
            "tier1_candidate_metrics": {
                item["model_name"]: item["tier1_validation_metrics"]
                for item in results
            },
            "test_metrics": test_metrics,
            "tier1_test_metrics": tier1_test_metrics,
            "selection_metric_scope": (
                "tier1_validation" if selected["tier1_validation_metrics"] else "all_validation"
            ),
            "is_calibrated": calibrator is not None,
            "calibration_guard": calibration_guard,
            "training_completed": True,
        }

        artifact_metadata = _save_artifacts(selected["model"], calibrator, dataset.feature_schema, report, version)
        model_version = _record_model_version(
            db,
            selected["model_name"],
            selected["model_type"],
            split,
            report,
            version,
            now,
            artifact_metadata,
        )
        report["model_version_id"] = model_version.id
        report["model_status"] = model_version.status
        _rewrite_training_reports(report, artifact_metadata)
        if promote_if_better or force_promote:
            from ml.training.model_promotion import auto_promote_if_better

            report["promotion_result"] = auto_promote_if_better(
                dev_allow_synthetic_promotion=dev_allow_synthetic_promotion,
                force=force_promote,
            )
        return report
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a candidate local prematch model.")
    parser.add_argument("--promote-if-better", action="store_true")
    parser.add_argument("--dev-allow-synthetic-promotion", action="store_true")
    parser.add_argument("--force-promote", action="store_true")
    parser.add_argument(
        "--training-profile",
        choices=["tier1_only", "tier1_plus_verified_pro"],
        default="tier1_only",
    )
    parser.add_argument("--feature-set", choices=["all", "differential"], default="all")
    args = parser.parse_args()
    try:
        report = train_prematch_model(
            promote_if_better=args.promote_if_better,
            dev_allow_synthetic_promotion=args.dev_allow_synthetic_promotion,
            force_promote=args.force_promote,
            training_profile=args.training_profile,
            feature_set=args.feature_set,
        )
    except NotEnoughTrainingDataError as exc:
        print(str(exc))
        return

    print(
        "Prematch ML training complete: "
        f"selected_model={report['selected_model']}, "
        f"rows={report['rows']}, "
        f"is_calibrated={report['is_calibrated']}, "
        f"model_version_id={report['model_version_id']}, "
        f"status={report['model_status']}"
    )


def _positive_class_probabilities(model, x: list[list[float]]) -> list[float]:
    return [float(row[1]) for row in model.predict_proba(x)]


def _metric_sort_value(value: float | None) -> float:
    return float(value) if value is not None else float("inf")


def _subset_metrics(
    labels: list[int],
    probabilities: list[float],
    sample_weights: list[float],
    *,
    min_rows: int = 5,
) -> dict[str, float | None] | None:
    indices = [index for index, weight in enumerate(sample_weights) if weight == 1.0]
    if len(indices) < min_rows:
        return None
    subset_labels = [labels[index] for index in indices]
    subset_probabilities = [probabilities[index] for index in indices]
    metrics = calculate_classification_metrics(subset_labels, subset_probabilities)
    metrics["sample_size"] = len(indices)
    return metrics


def _save_artifacts(model, calibrator, feature_schema, report: dict[str, Any], version: str) -> dict[str, str | None]:
    artifact_dir = Path(ML_ARTIFACT_DIR)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir = artifact_dir / "candidates" / version
    candidate_dir.mkdir(parents=True, exist_ok=True)
    model_path = candidate_dir / "prematch_model.pkl"
    calibrator_path = candidate_dir / "calibrator.pkl"
    schema_path = candidate_dir / "feature_schema.json"
    report_path = candidate_dir / "training_report.json"

    with model_path.open("wb") as file:
        pickle.dump(model, file)
    if calibrator is not None:
        with calibrator_path.open("wb") as file:
            pickle.dump(calibrator, file)

    with schema_path.open("w", encoding="utf-8") as file:
        json.dump(
            {**asdict(feature_schema), "feature_version": FEATURE_VERSION},
            file,
            indent=2,
            sort_keys=True,
        )
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)
    temp_report_path = TRAINING_REPORT_PATH.with_suffix(".tmp")
    with temp_report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)
    temp_report_path.replace(TRAINING_REPORT_PATH)
    return {
        "feature_version": FEATURE_VERSION,
        "model_path": str(model_path),
        "calibrator_path": str(calibrator_path) if calibrator is not None else None,
        "feature_schema_path": str(schema_path),
        "training_report_path": str(report_path),
    }


def _rewrite_training_reports(report: dict[str, Any], artifact_metadata: dict[str, str | None]) -> None:
    paths = [TRAINING_REPORT_PATH]
    candidate_report = artifact_metadata.get("training_report_path")
    if candidate_report:
        paths.append(Path(candidate_report))
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2, sort_keys=True, default=str)
        temporary.replace(path)


def _record_model_version(
    db,
    model_name: str,
    model_type: str,
    split,
    report: dict[str, Any],
    version: str,
    trained_at: datetime,
    artifact_metadata: dict[str, str | None],
) -> ModelVersion:
    model_version = ModelVersion(
        model_name=model_name,
        model_type=model_type,
        version=version,
        trained_at=trained_at,
        train_start_date=_start_date(split.train.rows),
        train_end_date=_end_date(split.train.rows),
        validation_start_date=_start_date(split.validation.rows),
        validation_end_date=_end_date(split.validation.rows),
        test_start_date=_start_date(split.test.rows),
        test_end_date=_end_date(split.test.rows),
        metrics_json=report,
        artifact_path=str(artifact_metadata["model_path"]),
        artifact_metadata_json=artifact_metadata,
        is_active=False,
        status="candidate",
    )
    db.add(model_version)
    db.commit()
    db.refresh(model_version)
    return model_version


def _start_date(rows) -> datetime | None:
    return rows[0].start_time if rows else None


def _end_date(rows) -> datetime | None:
    return rows[-1].start_time if rows else None


def _source_types(db, match_ids: list[int]) -> set[str]:
    if not match_ids:
        return set()
    return set(db.scalars(select(Match.external_source).where(Match.id.in_(match_ids))).all())


def _source_counts(db, match_ids: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not match_ids:
        return counts
    for source in db.scalars(select(Match.external_source).where(Match.id.in_(match_ids))).all():
        key = source or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _dataset_warnings(metadata: dict[str, Any]) -> list[str]:
    warnings = []
    if metadata.get("dataset_type") == "mixed":
        warnings.append("Dataset mixes dev_seed and non-dev rows; review before interpreting metrics.")
    if metadata.get("real_rows_count", 0) < 300:
        warnings.append("Fewer than 300 real Tier 1 rows; do not treat metrics as reliable real accuracy.")
    if metadata.get("dev_seed_rows_count", 0) > 0:
        warnings.append("Dataset includes synthetic dev_seed rows.")
    return warnings


def _dataset_type(sources: set[str]) -> str:
    if not sources:
        return "unknown"
    if sources == {"dev_seed"}:
        return "dev_seed"
    if "dev_seed" in sources:
        return "mixed"
    return "real"


if __name__ == "__main__":
    main()
