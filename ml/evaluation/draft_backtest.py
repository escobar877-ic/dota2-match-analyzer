from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
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
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, ModelVersion
from app.prediction.engine import FormulaPredictionEngine
from ml.config import ML_ARTIFACT_DIR
from ml.evaluation.metrics import calculate_classification_metrics
from ml.models import model_loader
from ml.training.draft_dataset_builder import (
    NotEnoughDraftTrainingDataError,
    build_draft_training_dataset,
)


DRAFT_BACKTEST_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "draft_backtest_report.json"


def run_draft_backtest(
    *,
    candidate_version: str | None = None,
    min_rows: int = 30,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        try:
            dataset = build_draft_training_dataset(db, min_rows=min_rows)
        except NotEnoughDraftTrainingDataError as exc:
            report = _empty_report(str(exc))
            _write_report(report)
            return report

        candidate = _select_draft_candidate(db, candidate_version)
        warnings = []
        if candidate is None:
            warnings.append("No draft-aware candidate model found.")
            report = _empty_report("No draft-aware candidate model found.")
            report["sample_size"] = len(dataset.rows)
            report["warnings"] = warnings
            _write_report(report)
            return report

        draft_model, draft_schema, draft_calibrator = _load_draft_artifacts(candidate)
        active_model_version = _get_active_model_version(db)
        active_model = model_loader.load_active_model() if active_model_version and model_loader.model_artifacts_exist() else None
        active_schema = model_loader.load_feature_schema() if active_model is not None else None
        active_calibrator = model_loader.load_calibrator() if active_model is not None else None

        records = []
        formula_engine = FormulaPredictionEngine()
        for row in dataset.rows:
            match = db.scalar(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(Match.id == row.match_id)
            )
            if match is None:
                continue
            formula_probability = formula_engine.predict(db, match).team_a_probability
            elo_probability = _elo_probability(row.features)
            prematch_ml_probability = None
            if active_model is not None and active_schema is not None:
                try:
                    prematch_ml_probability = _predict_probability(active_model, active_calibrator, row.features, active_schema)
                except Exception:
                    prematch_ml_probability = None
            draft_probability = _predict_probability(draft_model, draft_calibrator, row.features, draft_schema)
            available_for_ensemble = [
                value
                for value in [formula_probability, elo_probability, prematch_ml_probability]
                if value is not None
            ]
            ensemble_probability = sum(available_for_ensemble) / len(available_for_ensemble) if available_for_ensemble else None
            records.append(
                {
                    "match_id": row.match_id,
                    "label": row.label,
                    "formula": formula_probability,
                    "elo": elo_probability,
                    "prematch_ml": prematch_ml_probability,
                    "ensemble": ensemble_probability,
                    "draft_model": draft_probability,
                }
            )

        source_types = _source_types(db, [row.match_id for row in dataset.rows])
        dataset_type = _dataset_type(source_types)
        if dataset_type == "dev_seed":
            warnings.append("Synthetic dev seed draft backtest is not real accuracy.")
        elif dataset_type == "mixed":
            warnings.append("Draft backtest uses mixed dev/non-dev sources; review before interpreting metrics.")
        if len(records) < 50:
            warnings.append("Draft backtest sample is small.")

        metrics = _metrics_by_model(records)
        report = {
            "status": "warning" if warnings or len(records) < min_rows else "ok",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_type": dataset_type,
            "sample_size": len(records),
            "candidate_version": candidate.version,
            "candidate_model_version_id": candidate.id,
            "compared_models": sorted(metrics.keys()),
            "metrics": metrics,
            "best_by_log_loss": _best_model(metrics, "log_loss"),
            "best_by_brier_score": _best_model(metrics, "brier_score"),
            "warnings": warnings,
            "draft_model_used": True,
            "not_used_in_main_prediction": True,
        }
        _write_report(report)
        return report
    finally:
        db.close()


def _empty_report(message: str) -> dict[str, Any]:
    return {
        "status": "warning",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_type": "unknown",
        "sample_size": 0,
        "candidate_version": None,
        "compared_models": [],
        "metrics": {},
        "best_by_log_loss": None,
        "best_by_brier_score": None,
        "warnings": [message],
        "draft_model_used": False,
        "not_used_in_main_prediction": True,
    }


def _select_draft_candidate(db, candidate_version: str | None) -> ModelVersion | None:
    statement = select(ModelVersion).where(
        ModelVersion.status == "candidate",
        ModelVersion.is_active.is_(False),
    )
    if candidate_version:
        statement = statement.where(ModelVersion.version == candidate_version)
    candidates = db.scalars(statement.order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())).all()
    for candidate in candidates:
        if (candidate.artifact_metadata_json or {}).get("draft_aware") is True:
            return candidate
    return None


def _load_draft_artifacts(candidate: ModelVersion):
    metadata = candidate.artifact_metadata_json or {}
    with Path(metadata["model_path"]).open("rb") as file:
        model = pickle.load(file)
    schema = json.loads(Path(metadata["feature_schema_path"]).read_text(encoding="utf-8"))
    calibrator = None
    if metadata.get("calibrator_path") and Path(metadata["calibrator_path"]).exists():
        with Path(metadata["calibrator_path"]).open("rb") as file:
            calibrator = pickle.load(file)
    return model, schema, calibrator


def _predict_probability(model, calibrator, features: dict, schema: dict) -> float:
    encoded = _encode_features(features, schema)
    probability = float(model.predict_proba([encoded])[0][1])
    if calibrator is not None:
        probability = float(calibrator.predict_proba([probability])[0])
    return max(0.0001, min(0.9999, probability))


def _encode_features(features: dict, schema: dict) -> list[float]:
    names = schema.get("feature_names") or []
    maps = schema.get("categorical_maps") or {}
    fills = schema.get("fill_values") or {}
    encoded = []
    for name in names:
        value = features.get(name)
        if value is None:
            encoded.append(float(fills.get(name, 0.0)))
        elif isinstance(value, bool):
            encoded.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            encoded.append(float(value))
        elif isinstance(value, str):
            encoded.append(float(maps.get(name, {}).get(value, 0)))
        else:
            encoded.append(float(fills.get(name, 0.0)))
    return encoded


def _elo_probability(features: dict) -> float:
    elo_diff = features.get("elo_diff")
    if elo_diff is None:
        return 0.5
    return max(0.0001, min(0.9999, 1 / (1 + 10 ** (-float(elo_diff) / 400))))


def _metrics_by_model(records: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    labels = [record["label"] for record in records]
    metrics = {}
    for name in ["formula", "elo", "prematch_ml", "ensemble", "draft_model"]:
        values = [record[name] for record in records if record.get(name) is not None]
        model_labels = [record["label"] for record in records if record.get(name) is not None]
        if values and len(values) == len(model_labels):
            metrics[name] = calculate_classification_metrics(model_labels or labels, values)
    return metrics


def _best_model(metrics: dict[str, dict[str, float | None]], metric_name: str) -> str | None:
    valid = [(name, values.get(metric_name)) for name, values in metrics.items() if values.get(metric_name) is not None]
    if not valid:
        return None
    return min(valid, key=lambda item: float(item[1]))[0]


def _get_active_model_version(db) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


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


def _write_report(report: dict[str, Any]) -> None:
    DRAFT_BACKTEST_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DRAFT_BACKTEST_REPORT_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, default=str)
    temp_path.replace(DRAFT_BACKTEST_REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest experimental draft-aware candidate model.")
    parser.add_argument("--candidate-version")
    parser.add_argument("--output-json", action="store_true")
    parser.add_argument("--min-rows", type=int, default=30)
    args = parser.parse_args()
    report = run_draft_backtest(candidate_version=args.candidate_version, min_rows=args.min_rows)
    if args.output_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(
            "Draft backtest complete: "
            f"status={report['status']}, sample_size={report['sample_size']}, report_path={DRAFT_BACKTEST_REPORT_PATH}"
        )


if __name__ == "__main__":
    main()
