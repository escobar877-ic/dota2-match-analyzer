from __future__ import annotations

import json
import os
import pickle
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

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
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Backtest, Match, MatchPrematchFeature, ModelVersion
from app.prediction.engine import FormulaPredictionEngine
from ml.config import ML_ARTIFACT_DIR
from ml.evaluation.model_quality_report import build_model_quality_report
from ml.evaluation.metrics import calculate_classification_metrics
from ml.evaluation.calibration_report import build_calibration_report
from ml.models import model_loader
from ml.training.dataset_builder import NotEnoughTrainingDataError, build_training_dataset


BACKTEST_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "backtest_report.json"
MIN_BACKTEST_ROWS = 20
WARMUP_ROWS = 20


def run_backtest(*, model_version_id: int | None = None) -> dict | None:
    started_at = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        try:
            dataset = build_training_dataset(db, min_rows=MIN_BACKTEST_ROWS)
        except NotEnoughTrainingDataError as exc:
            print("Not enough Tier 1 historical matches for backtest.")
            return None

        if len(dataset.rows) <= WARMUP_ROWS:
            print("Not enough Tier 1 historical matches for backtest.")
            return None

        selected_model_version = (
            db.get(ModelVersion, model_version_id) if model_version_id is not None else _get_active_model_version(db)
        )
        if model_version_id is not None:
            ml_model, feature_schema, calibrator = _load_candidate_artifacts(selected_model_version)
            active_model_version = _get_active_model_version(db)
            active_model = model_loader.load_active_model() if model_loader.model_artifacts_exist() else None
            active_schema = model_loader.load_feature_schema() if active_model is not None else None
            active_calibrator = model_loader.load_calibrator() if active_model is not None else None
            active_feature_version = _model_feature_version(active_model_version)
        else:
            active_model_version = selected_model_version
            active_model = None
            active_schema = None
            active_calibrator = None
            ml_model = model_loader.load_active_model() if selected_model_version and model_loader.model_artifacts_exist() else None
            feature_schema = model_loader.load_feature_schema() if ml_model is not None else None
            calibrator = model_loader.load_calibrator() if ml_model is not None else None
            active_feature_version = _model_feature_version(active_model_version)
        ml_available = bool(selected_model_version and ml_model and feature_schema)
        selected_feature_version = _model_feature_version(selected_model_version)

        if selected_model_version and selected_model_version.test_start_date:
            eval_rows = [
                row
                for row in dataset.rows
                if row.start_time >= selected_model_version.test_start_date
                and (
                    selected_model_version.test_end_date is None
                    or row.start_time <= selected_model_version.test_end_date
                )
            ]
        else:
            eval_rows = dataset.rows[WARMUP_ROWS:]
        records = []
        formula_engine = FormulaPredictionEngine()
        for row in eval_rows:
            match = db.scalar(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(Match.id == row.match_id)
            )
            if match is None:
                continue
            formula_probability = formula_engine.predict(db, match).team_a_probability
            elo_probability = _elo_probability(row.features)
            ml_probability = None
            active_ml_probability = None
            if ml_available:
                selected_features = (
                    row.features
                    if model_version_id is not None
                    else _features_for_model_version(
                        db,
                        row.match_id,
                        selected_feature_version,
                    )
                )
                if selected_features is not None:
                    try:
                        ml_probability = _predict_ml_probability(
                            ml_model,
                            calibrator,
                            selected_features,
                            feature_schema,
                        )
                    except Exception:
                        ml_probability = None
            if model_version_id is not None and active_model is not None and active_schema:
                active_features = _features_for_model_version(
                    db,
                    row.match_id,
                    active_feature_version,
                )
                if active_features is not None:
                    try:
                        active_ml_probability = _predict_ml_probability(
                            active_model,
                            active_calibrator,
                            active_features,
                            active_schema,
                        )
                    except Exception:
                        active_ml_probability = None
            records.append(
                {
                    "match_id": row.match_id,
                    "start_time": row.start_time,
                    "label": row.label,
                    "formula": formula_probability,
                    "elo": elo_probability,
                    "ml": ml_probability,
                    "active_ml": active_ml_probability,
                }
            )

        if not records:
            print("Not enough Tier 1 historical matches for backtest.")
            return None

        source_types = _source_types(db, [record["match_id"] for record in records])
        source_counts = _source_counts(db, [record["match_id"] for record in records])
        dataset_type = _dataset_type(source_types)
        report = build_model_quality_report(records, dataset_type, ml_available=ml_available)
        if ml_available:
            ensemble_probabilities = [
                record["formula"] * 0.35 + record["elo"] * 0.25 + record["ml"] * 0.40
                for record in records
                if record.get("ml") is not None
            ]
            ensemble_labels = [
                record["label"] for record in records if record.get("ml") is not None
            ]
            ensemble_metrics = calculate_classification_metrics(
                ensemble_labels,
                ensemble_probabilities,
            )
            ensemble_metrics["calibration_error"] = build_calibration_report(
                ensemble_labels,
                ensemble_probabilities,
            ).get("calibration_error")
            report["candidate_ensemble"] = {
                "weights": {"formula": 0.35, "elo": 0.25, "ml": 0.40},
                "metrics": ensemble_metrics,
            }
        if model_version_id is not None:
            active_probabilities = [
                record["active_ml"] for record in records if record.get("active_ml") is not None
            ]
            active_labels = [
                record["label"] for record in records if record.get("active_ml") is not None
            ]
            active_metrics = calculate_classification_metrics(active_labels, active_probabilities)
            active_metrics["calibration_error"] = build_calibration_report(
                active_labels,
                active_probabilities,
            ).get("calibration_error")
            report["active_ml_comparison"] = {
                "model_version_id": active_model_version.id if active_model_version else None,
                "feature_version": active_feature_version,
                "rows_compared": len(active_probabilities),
                "metrics": active_metrics,
            }
        report["source_counts"] = source_counts
        report["real_rows_count"] = sum(count for source, count in source_counts.items() if source != "dev_seed")
        report["dev_seed_rows_count"] = source_counts.get("dev_seed", 0)
        report["total_rows"] = len(records)
        report["warnings"] = _dataset_warnings(report)
        report["date_from"] = records[0]["start_time"].isoformat()
        report["date_to"] = records[-1]["start_time"].isoformat()
        report["model_version_id"] = selected_model_version.id if selected_model_version else None
        report["model_feature_version"] = selected_feature_version
        report["dataset_feature_version"] = dataset.metadata.get("feature_version")
        report["candidate_backtest"] = model_version_id is not None
        report["evaluation_scope"] = (
            "saved_test_window"
            if selected_model_version and selected_model_version.test_start_date
            else "historical_after_warmup"
        )
        report_path = _write_report(report, model_version_id=model_version_id)
        _record_backtest(db, report, selected_model_version, records, started_at, report_path)
        print(
            "Backtest complete: "
            f"matches={len(records)}, dataset_type={dataset_type}, ml_available={ml_available}, "
            f"report_path={report_path}"
        )
        return report
    finally:
        db.close()


def _elo_probability(features: dict) -> float:
    elo_diff = features.get("elo_diff")
    if elo_diff is None:
        return 0.5
    return max(0.0001, min(0.9999, 1 / (1 + 10 ** (-float(elo_diff) / 400))))


def _predict_ml_probability(model, calibrator, features: dict, feature_schema: dict) -> float:
    encoded = _encode_features(features, feature_schema)
    probability = float(model.predict_proba([encoded])[0][1])
    if calibrator is not None:
        calibrated = calibrator.predict_proba([probability])
        first = calibrated[0]
        probability = float(first if isinstance(first, (float, int)) else first[1])
    return max(0.0001, min(0.9999, probability))


def _encode_features(features: dict, feature_schema: dict) -> list[float]:
    names = feature_schema.get("feature_names") or []
    maps = feature_schema.get("categorical_maps") or {}
    fills = feature_schema.get("fill_values") or {}
    values = []
    for name in names:
        value = features.get(name)
        if value is None:
            values.append(float(fills.get(name, 0.0)))
        elif isinstance(value, bool):
            values.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            values.append(float(value))
        elif isinstance(value, str):
            values.append(float(maps.get(name, {}).get(value, 0)))
        else:
            values.append(float(fills.get(name, 0.0)))
    return values


def _get_active_model_version(db) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


def _model_feature_version(model_version: ModelVersion | None) -> str:
    if model_version is None:
        return "prematch_v3"
    metadata = model_version.artifact_metadata_json or {}
    value = metadata.get("feature_version")
    if isinstance(value, str) and value:
        return value
    report = model_version.metrics_json or {}
    value = report.get("feature_version")
    if isinstance(value, str) and value:
        return value
    dataset_metadata = report.get("dataset_metadata") or {}
    value = dataset_metadata.get("feature_version")
    if isinstance(value, str) and value:
        return value
    return "prematch_v3"


def _features_for_model_version(
    db,
    match_id: int,
    feature_version: str,
) -> dict | None:
    record = db.scalar(
        select(MatchPrematchFeature)
        .where(
            MatchPrematchFeature.match_id == match_id,
            MatchPrematchFeature.feature_version == feature_version,
        )
        .order_by(MatchPrematchFeature.generated_at.desc(), MatchPrematchFeature.id.desc())
        .limit(1)
    )
    return dict(record.features_json) if record is not None else None


def _load_candidate_artifacts(model_version: ModelVersion | None):
    if model_version is None:
        return None, None, None
    metadata = model_version.artifact_metadata_json or {}
    model_path = Path(metadata.get("model_path") or model_version.artifact_path or "")
    schema_path = Path(metadata.get("feature_schema_path") or "")
    calibrator_value = metadata.get("calibrator_path")
    if not model_path.is_file() or not schema_path.is_file():
        return None, None, None
    with model_path.open("rb") as file:
        model = pickle.load(file)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    calibrator = None
    if calibrator_value and Path(calibrator_value).is_file():
        with Path(calibrator_value).open("rb") as file:
            calibrator = pickle.load(file)
    return model, schema, calibrator


def _source_types(db, match_ids: list[int]) -> set[str]:
    return set(db.scalars(select(Match.external_source).where(Match.id.in_(match_ids))).all())


def _source_counts(db, match_ids: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in db.scalars(select(Match.external_source).where(Match.id.in_(match_ids))).all():
        key = source or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _dataset_type(sources: set[str]) -> str:
    if not sources:
        return "unknown"
    if sources == {"dev_seed"}:
        return "dev_seed"
    if "dev_seed" in sources:
        return "mixed"
    return "real"


def _dataset_warnings(report: dict) -> list[str]:
    warnings = []
    if report.get("dataset_type") == "mixed":
        warnings.append("Backtest mixes dev_seed and non-dev rows; review before interpreting metrics.")
    if report.get("real_rows_count", 0) < 300:
        warnings.append(
            "Evaluation window has fewer than 300 real Tier 1 matches; treat accuracy estimates as preliminary."
        )
    if report.get("dev_seed_rows_count", 0) > 0:
        warnings.append("Backtest includes synthetic dev_seed rows.")
    return warnings


def _write_report(report: dict, *, model_version_id: int | None = None) -> Path:
    report_path = (
        Path(ML_ARTIFACT_DIR) / f"candidate_backtest_report_{model_version_id}.json"
        if model_version_id is not None
        else BACKTEST_REPORT_PATH
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)
    temp_path.replace(report_path)
    return report_path


def _record_backtest(
    db,
    report: dict,
    model_version: ModelVersion | None,
    records: list[dict],
    started_at: datetime,
    report_path: Path,
) -> None:
    finished_at = datetime.now(timezone.utc)
    db.add(
        Backtest(
            model_version_id=model_version.id if model_version else None,
            started_at=started_at,
            finished_at=finished_at,
            date_from=records[0]["start_time"],
            date_to=records[-1]["start_time"],
            dataset_type=report["dataset_type"],
            matches_count=len(records),
            metrics_json=report,
            report_path=str(report_path),
        )
    )
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the active or a specific candidate model.")
    parser.add_argument("--model-version", type=int)
    args = parser.parse_args()
    run_backtest(model_version_id=args.model_version)


if __name__ == "__main__":
    main()
