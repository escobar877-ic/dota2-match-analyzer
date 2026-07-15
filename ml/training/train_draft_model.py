from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import sys
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
from ml.features.draft_feature_schema import FEATURE_VERSION
from ml.models.calibration import calibrate_probabilities
from ml.models.logistic_regression_model import create_logistic_regression_model
from ml.models.random_forest_model import create_random_forest_model
from ml.safety import assert_local_ml_only, assert_no_forbidden_packages
from ml.training.dataset_builder import NotEnoughTrainingDataError, split_time_based
from ml.training.draft_dataset_builder import (
    NotEnoughDraftTrainingDataError,
    build_draft_training_dataset,
)


DRAFT_CANDIDATES_DIR = Path(ML_ARTIFACT_DIR) / "draft_candidates"


class DraftTrainingError(RuntimeError):
    pass


def train_draft_model(
    *,
    min_rows: int = 50,
    model: str = "auto",
    output_json: bool = False,
    no_calibration: bool = False,
) -> dict[str, Any]:
    assert_local_ml_only()
    assert_no_forbidden_packages()
    if model not in {"auto", "logistic_regression", "random_forest"}:
        raise DraftTrainingError(f"Unsupported draft model: {model}")

    db = SessionLocal()
    tmp_dir: Path | None = None
    artifact_dir: Path | None = None
    try:
        dataset = build_draft_training_dataset(db, min_rows=min_rows)
        if len(set(dataset.y)) < 2:
            raise DraftTrainingError("Draft-aware target has one class only; cannot train a classifier safely.")
        split = split_time_based(dataset)
        if len(set(split.train.y)) < 2:
            raise DraftTrainingError("Draft-aware training split has one class only; cannot train safely.")

        candidates = _candidate_models(model)
        results = []
        for model_name, model_type, candidate in candidates:
            candidate.fit(split.train.x, split.train.y)
            validation_probabilities = _positive_class_probabilities(candidate, split.validation.x)
            validation_metrics = calculate_classification_metrics(split.validation.y, validation_probabilities)
            results.append(
                {
                    "model_name": model_name,
                    "model_type": model_type,
                    "model": candidate,
                    "validation_probabilities": validation_probabilities,
                    "validation_metrics": validation_metrics,
                }
            )

        if not results:
            raise DraftTrainingError("No draft-aware candidate model could be trained.")
        selected = min(
            results,
            key=lambda item: (
                _metric_sort_value(item["validation_metrics"].get("log_loss")),
                _metric_sort_value(item["validation_metrics"].get("brier_score")),
            ),
        )
        calibrator = None if no_calibration else calibrate_probabilities(selected["validation_probabilities"], split.validation.y)
        test_probabilities = _positive_class_probabilities(selected["model"], split.test.x)
        if calibrator is not None:
            test_probabilities = calibrator.predict_proba(test_probabilities)
        test_metrics = calculate_classification_metrics(split.test.y, test_probabilities)

        now = datetime.now(timezone.utc)
        version = f"draft_{now.strftime('%Y%m%d%H%M%S')}"
        source_types = _source_types(db, [row.match_id for row in dataset.rows])
        dataset_type = _dataset_type(source_types)
        warnings = []
        if dataset_type == "dev_seed":
            warnings.append("Synthetic dev seed draft model is not real accuracy.")
        elif dataset_type == "mixed":
            warnings.append("Draft model uses mixed dev/non-dev sources; review before interpreting metrics.")

        report = {
            "version": version,
            "selected_model": selected["model_name"],
            "selected_model_type": selected["model_type"],
            "feature_version": FEATURE_VERSION,
            "rows_count": len(dataset.rows),
            "train_rows": len(split.train.rows),
            "validation_rows": len(split.validation.rows),
            "test_rows": len(split.test.rows),
            "dataset_type": dataset_type,
            "source_types": sorted(source_types),
            "candidate_metrics": {item["model_name"]: item["validation_metrics"] for item in results},
            "test_metrics": test_metrics,
            "is_calibrated": calibrator is not None,
            "warnings": warnings,
            "experimental": True,
            "not_used_in_main_prediction": True,
            "training_completed": True,
        }
        tmp_dir, artifact_dir, artifact_metadata = _save_artifacts_atomic(
            model=selected["model"],
            calibrator=calibrator,
            feature_schema=dataset.feature_schema,
            report=report,
            version=version,
        )
        _validate_artifacts(artifact_dir, dataset)
        artifact_metadata.update(
            {
                "draft_aware": True,
                "experimental": True,
                "feature_version": FEATURE_VERSION,
                "artifact_dir": str(artifact_dir),
                "rows_count": len(dataset.rows),
                "train_rows": len(split.train.rows),
                "validation_rows": len(split.validation.rows),
                "dataset_type": dataset_type,
                "selected_model": selected["model_name"],
                "metrics": test_metrics,
                "warnings": warnings,
                "not_used_in_main_prediction": True,
            }
        )
        _write_metadata_file(artifact_dir, artifact_metadata)
        model_version = _record_model_version(
            db=db,
            model_name=f"draft_{selected['model_name']}",
            model_type=selected["model_type"],
            version=version,
            trained_at=now,
            split=split,
            report=report,
            artifact_metadata=artifact_metadata,
        )
        report["model_version_id"] = model_version.id
        report["model_status"] = model_version.status
        _rewrite_report_with_model_id(artifact_dir, report)
        return report
    except (NotEnoughDraftTrainingDataError, NotEnoughTrainingDataError, DraftTrainingError) as exc:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        if artifact_dir is not None and artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        return {
            "status": "failed",
            "error": str(exc),
            "feature_version": FEATURE_VERSION,
            "not_used_in_main_prediction": True,
        }
    except Exception:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        if artifact_dir is not None and artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        raise
    finally:
        db.close()


def _candidate_models(model: str):
    candidates = []
    if model in {"auto", "logistic_regression"}:
        candidates.append(("logistic_regression", "sklearn", create_logistic_regression_model()))
    if model in {"auto", "random_forest"}:
        candidates.append(("random_forest", "sklearn", create_random_forest_model()))
    return candidates


def _positive_class_probabilities(model, x: list[list[float]]) -> list[float]:
    return [float(row[1]) for row in model.predict_proba(x)]


def _metric_sort_value(value: float | None) -> float:
    return float(value) if value is not None else float("inf")


def _save_artifacts_atomic(*, model, calibrator, feature_schema, report: dict[str, Any], version: str):
    DRAFT_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = DRAFT_CANDIDATES_DIR / f"{version}.tmp"
    artifact_dir = DRAFT_CANDIDATES_DIR / version
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    tmp_dir.mkdir(parents=True)

    model_path = tmp_dir / "draft_model.pkl"
    calibrator_path = tmp_dir / "draft_calibrator.pkl"
    schema_path = tmp_dir / "draft_feature_schema.json"
    report_path = tmp_dir / "draft_training_report.json"
    metadata_path = tmp_dir / "draft_model_metadata.json"

    with model_path.open("wb") as file:
        pickle.dump(model, file)
    if calibrator is not None:
        with calibrator_path.open("wb") as file:
            pickle.dump(calibrator, file)
    schema_payload = asdict(feature_schema)
    schema_payload["feature_version"] = FEATURE_VERSION
    with schema_path.open("w", encoding="utf-8") as file:
        json.dump(schema_payload, file, indent=2, sort_keys=True)
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)
    metadata = {
        "model_path": str(artifact_dir / "draft_model.pkl"),
        "calibrator_path": str(artifact_dir / "draft_calibrator.pkl") if calibrator is not None else None,
        "feature_schema_path": str(artifact_dir / "draft_feature_schema.json"),
        "training_report_path": str(artifact_dir / "draft_training_report.json"),
        "metadata_path": str(artifact_dir / "draft_model_metadata.json"),
    }
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2, sort_keys=True)
    tmp_dir.replace(artifact_dir)
    return tmp_dir, artifact_dir, metadata


def _validate_artifacts(artifact_dir: Path, dataset) -> None:
    required = ["draft_model.pkl", "draft_feature_schema.json", "draft_training_report.json"]
    missing = [name for name in required if not (artifact_dir / name).exists()]
    if missing:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise DraftTrainingError(f"Draft artifact validation failed; missing: {', '.join(missing)}")
    with (artifact_dir / "draft_feature_schema.json").open("r", encoding="utf-8") as file:
        schema = json.load(file)
    if schema.get("feature_version") != FEATURE_VERSION:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise DraftTrainingError("Draft artifact validation failed; schema version mismatch.")
    if len(schema.get("feature_names") or []) != len(dataset.feature_schema.feature_names):
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise DraftTrainingError("Draft artifact validation failed; feature count mismatch.")
    with (artifact_dir / "draft_model.pkl").open("rb") as file:
        model = pickle.load(file)
    probabilities = model.predict_proba([dataset.x[0]])[0]
    if len(probabilities) != 2 or abs(float(sum(probabilities)) - 1.0) > 0.000001:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise DraftTrainingError("Draft artifact validation failed; predict_proba is invalid.")


def _rewrite_report_with_model_id(artifact_dir: Path, report: dict[str, Any]) -> None:
    with (artifact_dir / "draft_training_report.json").open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)


def _write_metadata_file(artifact_dir: Path, artifact_metadata: dict[str, Any]) -> None:
    with (artifact_dir / "draft_model_metadata.json").open("w", encoding="utf-8") as file:
        json.dump(artifact_metadata, file, indent=2, sort_keys=True, default=str)


def _record_model_version(
    *,
    db,
    model_name: str,
    model_type: str,
    version: str,
    trained_at: datetime,
    split,
    report: dict[str, Any],
    artifact_metadata: dict[str, Any],
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


def _dataset_type(sources: set[str]) -> str:
    if not sources:
        return "unknown"
    if sources == {"dev_seed"}:
        return "dev_seed"
    if "dev_seed" in sources:
        return "mixed"
    return "real"


def main() -> None:
    parser = argparse.ArgumentParser(description="Train experimental draft-aware candidate model.")
    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--model", choices=["logistic_regression", "random_forest", "auto"], default="auto")
    parser.add_argument("--output-json", action="store_true")
    parser.add_argument("--no-calibration", action="store_true")
    args = parser.parse_args()
    report = train_draft_model(
        min_rows=args.min_rows,
        model=args.model,
        output_json=args.output_json,
        no_calibration=args.no_calibration,
    )
    if args.output_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif report.get("status") == "failed":
        print(f"Draft model training skipped: {report['error']}")
    else:
        print(
            "Draft model candidate trained: "
            f"version={report['version']}, selected_model={report['selected_model']}, rows={report['rows_count']}"
        )


if __name__ == "__main__":
    main()
