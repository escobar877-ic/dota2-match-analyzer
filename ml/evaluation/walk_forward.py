from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

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
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.db.models import Match, ModelVersion
from app.prediction.engine import FormulaPredictionEngine
from app.prediction.ensemble_weighting import (
    build_weighting_decision,
    get_latest_backtest_metrics,
)
from ml.config import ML_ARTIFACT_DIR
from ml.evaluation.backtest import _elo_probability
from ml.evaluation.calibration_report import build_calibration_report
from ml.evaluation.metrics import calculate_classification_metrics
from ml.features.feature_schema import FEATURE_VERSION
from ml.models.calibration import calibrate_probabilities_guarded
from ml.models.extra_trees_model import create_extra_trees_model
from ml.models.logistic_regression_model import create_logistic_regression_model
from ml.models.random_forest_model import create_random_forest_model
from ml.training.dataset_builder import (
    DatasetRow,
    NotEnoughTrainingDataError,
    TrainingDataset,
    build_training_dataset,
)


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "walk_forward_report.json"
DEFAULT_FOLDS = 5
DEFAULT_MIN_TRAIN_ROWS = 400
DEFAULT_MIN_EVAL_ROWS = 20
ENSEMBLE_WEIGHTS = {"formula": 0.35, "elo": 0.25, "ml": 0.40}
MIN_ENSEMBLE_WEIGHT = 0.10
MAX_ENSEMBLE_WEIGHT = 0.65
WEIGHT_GRID_STEP = 0.05


@dataclass(frozen=True)
class WalkForwardFold:
    number: int
    train_indices: list[int]
    evaluation_indices: list[int]


def build_walk_forward_folds(
    rows: Sequence[DatasetRow],
    *,
    folds_count: int = DEFAULT_FOLDS,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    min_eval_rows: int = DEFAULT_MIN_EVAL_ROWS,
) -> list[WalkForwardFold]:
    if folds_count < 1:
        raise ValueError("folds_count must be at least 1.")
    if min_train_rows < 2:
        raise ValueError("min_train_rows must be at least 2.")

    tier1_indices = [
        index
        for index, row in enumerate(rows)
        if index >= min_train_rows and row.sample_weight == 1.0
    ]
    if len(tier1_indices) < min_eval_rows:
        return []

    usable_folds = min(folds_count, len(tier1_indices) // min_eval_rows)
    if usable_folds < 1:
        return []

    chunks = _balanced_chunks(tier1_indices, usable_folds)
    folds: list[WalkForwardFold] = []
    previous_eval_end: datetime | None = None
    for number, chunk in enumerate(chunks, start=1):
        evaluation_start = rows[chunk[0]].start_time
        evaluation_end = rows[chunk[-1]].start_time
        train_indices = [
            index
            for index, row in enumerate(rows)
            if row.start_time < evaluation_start
        ]
        evaluation_indices = [
            index
            for index in chunk
            if rows[index].start_time >= evaluation_start
            and rows[index].start_time <= evaluation_end
            and rows[index].sample_weight == 1.0
        ]
        if len(train_indices) < min_train_rows or len(evaluation_indices) < min_eval_rows:
            continue
        if previous_eval_end is not None and evaluation_start <= previous_eval_end:
            continue
        folds.append(
            WalkForwardFold(
                number=number,
                train_indices=train_indices,
                evaluation_indices=evaluation_indices,
            )
        )
        previous_eval_end = evaluation_end
    return folds


def evaluate_stability(
    fold_reports: list[dict[str, Any]],
    aggregate_metrics: dict[str, dict[str, float | None]],
    *,
    min_valid_folds: int = 3,
    min_total_evaluation_rows: int = 100,
) -> dict[str, Any]:
    reasons: list[str] = []
    total_rows = sum(int(fold.get("evaluation_rows", 0)) for fold in fold_reports)
    ml_metrics = aggregate_metrics.get("ml") or {}
    elo_metrics = aggregate_metrics.get("elo") or {}

    if len(fold_reports) < min_valid_folds:
        reasons.append(f"Only {len(fold_reports)} valid folds; at least {min_valid_folds} are required.")
    if total_rows < min_total_evaluation_rows:
        reasons.append(
            f"Only {total_rows} Tier 1 evaluation rows; at least {min_total_evaluation_rows} are required."
        )

    ml_log_loss = _as_float(ml_metrics.get("log_loss"))
    elo_log_loss = _as_float(elo_metrics.get("log_loss"))
    if ml_log_loss is None:
        reasons.append("ML aggregate log_loss is unavailable.")
    elif elo_log_loss is not None and ml_log_loss > elo_log_loss + 0.01:
        reasons.append("ML aggregate log_loss is materially worse than the Elo baseline.")

    ml_brier = _as_float(ml_metrics.get("brier_score"))
    elo_brier = _as_float(elo_metrics.get("brier_score"))
    if ml_brier is None:
        reasons.append("ML aggregate brier_score is unavailable.")
    elif elo_brier is not None and ml_brier > elo_brier + 0.01:
        reasons.append("ML aggregate brier_score is materially worse than the Elo baseline.")

    calibration_error = _as_float(ml_metrics.get("calibration_error"))
    if calibration_error is None:
        reasons.append("ML calibration error is unavailable.")
    elif calibration_error > 0.12:
        reasons.append("ML aggregate calibration error exceeds 0.12.")

    worst_fold_log_loss = max(
        (
            float(fold["metrics"]["ml"]["log_loss"])
            for fold in fold_reports
            if fold.get("metrics", {}).get("ml", {}).get("log_loss") is not None
        ),
        default=None,
    )
    if worst_fold_log_loss is not None and worst_fold_log_loss > 0.75:
        reasons.append("At least one temporal fold has ML log_loss above 0.75.")

    passed = not reasons
    return {
        "passed": passed,
        "valid_folds": len(fold_reports),
        "total_tier1_evaluation_rows": total_rows,
        "thresholds": {
            "min_valid_folds": min_valid_folds,
            "min_total_evaluation_rows": min_total_evaluation_rows,
            "max_ml_vs_elo_metric_delta": 0.01,
            "max_calibration_error": 0.12,
            "max_single_fold_log_loss": 0.75,
        },
        "reasons": reasons,
        "recommendation": (
            "model_is_temporally_stable_keep_current_guarded_ensemble_weight"
            if passed
            else "do_not_increase_ml_weight_collect_more_data_and_review_failed_folds"
        ),
    }


def run_walk_forward_validation(
    *,
    folds_count: int = DEFAULT_FOLDS,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    min_eval_rows: int = DEFAULT_MIN_EVAL_ROWS,
    model_name: str = "auto",
    training_profile: str = "tier1_plus_verified_pro",
    feature_set: str = "differential",
    output_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        dataset = build_training_dataset(
            db,
            min_rows=min_train_rows + min_eval_rows,
            training_profile=training_profile,
            feature_set=feature_set,
        )
        active_model = _get_active_model(db)
        resolved_model_name = _resolve_model_name(model_name, active_model)
        baseline_weighting = build_weighting_decision(get_latest_backtest_metrics(db))
        baseline_weights = baseline_weighting.weights
        folds = build_walk_forward_folds(
            dataset.rows,
            folds_count=folds_count,
            min_train_rows=min_train_rows,
            min_eval_rows=min_eval_rows,
        )
        if not folds:
            raise NotEnoughTrainingDataError(
                "Not enough chronological Tier 1 evaluation rows for walk-forward validation."
            )

        formula_engine = FormulaPredictionEngine()
        fold_reports: list[dict[str, Any]] = []
        aggregate_predictions = {
            "formula": [],
            "elo": [],
            "ml_uncalibrated": [],
            "ml": [],
            "ensemble": [],
        }
        aggregate_labels: list[int] = []
        fold_outputs: list[dict[str, Any]] = []

        for fold in folds:
            fold_report, labels, predictions = _evaluate_fold(
                db,
                dataset,
                fold,
                resolved_model_name,
                formula_engine,
                baseline_weights,
            )
            fold_reports.append(fold_report)
            fold_outputs.append({"labels": labels, "predictions": predictions})
            aggregate_labels.extend(labels)
            for component, values in predictions.items():
                aggregate_predictions[component].extend(values)

        aggregate_metrics = {
            component: _metrics_with_calibration(aggregate_labels, probabilities)
            for component, probabilities in aggregate_predictions.items()
        }
        stability_gate = evaluate_stability(fold_reports, aggregate_metrics)
        weight_optimization = build_weight_optimization_report(
            fold_outputs,
            baseline_weights=baseline_weights,
            stability_passed=stability_gate["passed"],
        )
        optimized_weights = weight_optimization.get("recommended_weights") or baseline_weights
        optimized_probabilities = _weighted_probabilities(
            {
                name: aggregate_predictions[name]
                for name in ("formula", "elo", "ml")
            },
            optimized_weights,
        )
        aggregate_metrics["ensemble_optimized_oof"] = _metrics_with_calibration(
            aggregate_labels,
            optimized_probabilities,
        )
        tier1_rows = sum(1 for row in dataset.rows if row.sample_weight == 1.0)
        verified_pro_rows = len(dataset.rows) - tier1_rows
        report = {
            "status": "ok" if stability_gate["passed"] else "warning",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "feature_version": dataset.metadata.get("feature_version", FEATURE_VERSION),
            "feature_set": feature_set,
            "training_profile": training_profile,
            "model_name": resolved_model_name,
            "active_model_version_id": active_model.id if active_model else None,
            "active_model_version": active_model.version if active_model else None,
            "dataset": {
                "total_rows": len(dataset.rows),
                "tier1_rows": tier1_rows,
                "verified_pro_rows": verified_pro_rows,
                "evaluation_scope": "tier1_only",
                "training_scope": training_profile,
                "date_from": dataset.rows[0].start_time.isoformat(),
                "date_to": dataset.rows[-1].start_time.isoformat(),
            },
            "folds": fold_reports,
            "aggregate_metrics": aggregate_metrics,
            "ensemble_weights_for_evaluation": baseline_weights,
            "ensemble_weight_source_for_evaluation": baseline_weighting.weight_source,
            "weight_optimization": weight_optimization,
            "stability_gate": stability_gate,
            "warnings": [
                "Walk-forward results are preliminary until the project has broader real Tier 1 coverage."
            ],
            "production_changes": {
                "prediction_math_changed": False,
                "active_model_changed": False,
                "promotion_performed": False,
            },
        }
        _write_report(report, output_path)
        return report
    finally:
        db.close()


def _evaluate_fold(
    db: Session,
    dataset: TrainingDataset,
    fold: WalkForwardFold,
    model_name: str,
    formula_engine: FormulaPredictionEngine,
    ensemble_weights: dict[str, float],
) -> tuple[dict[str, Any], list[int], dict[str, list[float]]]:
    calibration_rows = max(10, int(len(fold.train_indices) * 0.15))
    core_indices = fold.train_indices[:-calibration_rows]
    calibration_indices = fold.train_indices[-calibration_rows:]
    if len(core_indices) < 2:
        raise NotEnoughTrainingDataError("Not enough fold training rows before calibration split.")

    model = _create_model(model_name)
    train_x = [dataset.x[index] for index in core_indices]
    train_y = [dataset.y[index] for index in core_indices]
    train_weights = [dataset.sample_weights[index] for index in core_indices]
    if len(set(train_y)) < 2:
        raise NotEnoughTrainingDataError("A walk-forward training fold has only one target class.")
    _fit_model(model, train_x, train_y, train_weights)

    calibration_x = [dataset.x[index] for index in calibration_indices]
    calibration_y = [dataset.y[index] for index in calibration_indices]
    calibration_probabilities = _positive_probabilities(model, calibration_x)
    calibrator, calibration_guard = calibrate_probabilities_guarded(
        calibration_probabilities,
        calibration_y,
    )

    labels: list[int] = []
    predictions = {
        "formula": [],
        "elo": [],
        "ml_uncalibrated": [],
        "ml": [],
        "ensemble": [],
    }
    for index in fold.evaluation_indices:
        row = dataset.rows[index]
        match = db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == row.match_id)
        )
        if match is None:
            continue
        formula_probability = formula_engine.predict(db, match).team_a_probability
        elo_probability = _elo_probability(row.features)
        raw_ml_probability = _positive_probabilities(model, [dataset.x[index]])[0]
        ml_probability = raw_ml_probability
        if calibrator is not None:
            ml_probability = float(calibrator.predict_proba([ml_probability])[0])
        ml_probability = min(0.9999, max(0.0001, ml_probability))
        ensemble_probability = (
            formula_probability * ensemble_weights["formula"]
            + elo_probability * ensemble_weights["elo"]
            + ml_probability * ensemble_weights["ml"]
        )
        labels.append(row.label)
        predictions["formula"].append(formula_probability)
        predictions["elo"].append(elo_probability)
        predictions["ml_uncalibrated"].append(raw_ml_probability)
        predictions["ml"].append(ml_probability)
        predictions["ensemble"].append(ensemble_probability)

    if not labels:
        raise NotEnoughTrainingDataError("A walk-forward fold has no evaluable Tier 1 matches.")
    train_rows = [dataset.rows[index] for index in fold.train_indices]
    evaluation_rows = [dataset.rows[index] for index in fold.evaluation_indices]
    metrics = {
        component: _metrics_with_calibration(labels, values)
        for component, values in predictions.items()
    }
    report = {
        "fold": fold.number,
        "train_rows": len(fold.train_indices),
        "core_train_rows": len(core_indices),
        "calibration_rows": len(calibration_indices),
        "evaluation_rows": len(labels),
        "train_start": train_rows[0].start_time.isoformat(),
        "train_end": train_rows[-1].start_time.isoformat(),
        "evaluation_start": evaluation_rows[0].start_time.isoformat(),
        "evaluation_end": evaluation_rows[-1].start_time.isoformat(),
        "calibrated": calibrator is not None,
        "calibration_guard": calibration_guard,
        "metrics": metrics,
    }
    return report, labels, predictions


def optimize_ensemble_weights(
    labels: list[int],
    component_predictions: dict[str, list[float]],
) -> dict[str, float]:
    required = ("formula", "elo", "ml")
    if not labels or any(len(component_predictions.get(name, [])) != len(labels) for name in required):
        raise ValueError("Formula, Elo and ML predictions must align with labels.")

    step_units = round(1.0 / WEIGHT_GRID_STEP)
    min_units = round(MIN_ENSEMBLE_WEIGHT / WEIGHT_GRID_STEP)
    max_units = round(MAX_ENSEMBLE_WEIGHT / WEIGHT_GRID_STEP)
    best_weights: dict[str, float] | None = None
    best_objective: tuple[float, float] | None = None
    for formula_units in range(min_units, max_units + 1):
        for elo_units in range(min_units, max_units + 1):
            ml_units = step_units - formula_units - elo_units
            if ml_units < min_units or ml_units > max_units:
                continue
            weights = {
                "formula": formula_units * WEIGHT_GRID_STEP,
                "elo": elo_units * WEIGHT_GRID_STEP,
                "ml": ml_units * WEIGHT_GRID_STEP,
            }
            probabilities = _weighted_probabilities(component_predictions, weights)
            metrics = calculate_classification_metrics(labels, probabilities)
            objective = (float(metrics["log_loss"]), float(metrics["brier_score"]))
            if best_objective is None or objective < best_objective:
                best_objective = objective
                best_weights = weights
    if best_weights is None:
        raise ValueError("No ensemble weight combination satisfied safety limits.")
    return {name: round(value, 4) for name, value in best_weights.items()}


def build_weight_optimization_report(
    fold_outputs: list[dict[str, Any]],
    *,
    baseline_weights: dict[str, float],
    stability_passed: bool,
) -> dict[str, Any]:
    if len(fold_outputs) < 3:
        return {
            "status": "insufficient",
            "production_approved": False,
            "recommended_weights": baseline_weights,
            "production_weights": baseline_weights,
            "reasons": ["At least three chronological folds are required for weight validation."],
        }

    selection_outputs = fold_outputs[:-1]
    validation_output = fold_outputs[-1]
    selection_labels, selection_predictions = _combine_fold_outputs(selection_outputs)
    validation_labels = list(validation_output["labels"])
    validation_predictions = {
        name: list(validation_output["predictions"][name])
        for name in ("formula", "elo", "ml")
    }
    candidate_weights = optimize_ensemble_weights(selection_labels, selection_predictions)
    selection_candidate = _metrics_with_calibration(
        selection_labels,
        _weighted_probabilities(selection_predictions, candidate_weights),
    )
    validation_candidate = _metrics_with_calibration(
        validation_labels,
        _weighted_probabilities(validation_predictions, candidate_weights),
    )
    validation_baseline = _metrics_with_calibration(
        validation_labels,
        _weighted_probabilities(validation_predictions, baseline_weights),
    )
    validation_components = {
        name: _metrics_with_calibration(validation_labels, values)
        for name, values in validation_predictions.items()
    }

    fold_comparisons = []
    improving_folds = 0
    for index, output in enumerate(fold_outputs, start=1):
        labels = list(output["labels"])
        predictions = {
            name: list(output["predictions"][name])
            for name in ("formula", "elo", "ml")
        }
        candidate_metrics = _metrics_with_calibration(
            labels,
            _weighted_probabilities(predictions, candidate_weights),
        )
        baseline_metrics = _metrics_with_calibration(
            labels,
            _weighted_probabilities(predictions, baseline_weights),
        )
        improved = (
            float(candidate_metrics["log_loss"]) <= float(baseline_metrics["log_loss"])
            and float(candidate_metrics["brier_score"]) <= float(baseline_metrics["brier_score"])
        )
        improving_folds += int(improved)
        fold_comparisons.append(
            {
                "fold": index,
                "sample_size": len(labels),
                "candidate": candidate_metrics,
                "baseline": baseline_metrics,
                "improved_both": improved,
            }
        )

    best_component_log_loss = min(
        float(metrics["log_loss"]) for metrics in validation_components.values()
    )
    best_component_brier = min(
        float(metrics["brier_score"]) for metrics in validation_components.values()
    )
    reasons: list[str] = []
    if not stability_passed:
        reasons.append("Walk-forward stability gate did not pass.")
    if len(selection_labels) < 100:
        reasons.append("Weight selection requires at least 100 earlier-fold evaluation rows.")
    if len(validation_labels) < 20:
        reasons.append("Weight validation requires at least 20 untouched latest-fold rows.")
    if float(validation_candidate["log_loss"]) > float(validation_baseline["log_loss"]) - 0.001:
        reasons.append("Candidate did not improve validation log_loss by at least 0.001.")
    if float(validation_candidate["brier_score"]) > float(validation_baseline["brier_score"]) - 0.0005:
        reasons.append("Candidate did not improve validation Brier score by at least 0.0005.")
    if float(validation_candidate["log_loss"]) > best_component_log_loss + 0.002:
        reasons.append("Candidate is materially worse than the best validation component by log_loss.")
    if float(validation_candidate["brier_score"]) > best_component_brier + 0.001:
        reasons.append("Candidate is materially worse than the best validation component by Brier score.")
    minimum_improving_folds = math.ceil(len(fold_outputs) / 2)
    if improving_folds < minimum_improving_folds:
        reasons.append(
            f"Candidate improved both metrics in only {improving_folds}/{len(fold_outputs)} folds."
        )

    approved = not reasons
    return {
        "status": "approved" if approved else "rejected",
        "method": "chronological_grid_search_v1",
        "selection_folds": len(selection_outputs),
        "selection_rows": len(selection_labels),
        "validation_fold": len(fold_outputs),
        "validation_rows": len(validation_labels),
        "weight_limits": {"min": MIN_ENSEMBLE_WEIGHT, "max": MAX_ENSEMBLE_WEIGHT},
        "baseline_weights": baseline_weights,
        "recommended_weights": candidate_weights,
        "production_weights": candidate_weights if approved else baseline_weights,
        "selection_candidate_metrics": selection_candidate,
        "validation_candidate_metrics": validation_candidate,
        "validation_baseline_metrics": validation_baseline,
        "validation_component_metrics": validation_components,
        "improving_folds": improving_folds,
        "required_improving_folds": minimum_improving_folds,
        "fold_comparisons": fold_comparisons,
        "production_approved": approved,
        "reasons": reasons,
        "recommendation": (
            "use_walk_forward_weights"
            if approved
            else "keep_existing_weights_and_collect_more_prospective_results"
        ),
    }


def _combine_fold_outputs(
    outputs: list[dict[str, Any]],
) -> tuple[list[int], dict[str, list[float]]]:
    labels: list[int] = []
    predictions = {"formula": [], "elo": [], "ml": []}
    for output in outputs:
        labels.extend(output["labels"])
        for name in predictions:
            predictions[name].extend(output["predictions"][name])
    return labels, predictions


def _weighted_probabilities(
    component_predictions: dict[str, list[float]],
    weights: dict[str, float],
) -> list[float]:
    names = ("formula", "elo", "ml")
    lengths = {len(component_predictions.get(name, [])) for name in names}
    if len(lengths) != 1:
        raise ValueError("Component prediction lengths must match.")
    rows_count = lengths.pop() if lengths else 0
    return [
        min(
            0.9999,
            max(
                0.0001,
                sum(
                    float(component_predictions[name][index]) * float(weights[name])
                    for name in names
                ),
            ),
        )
        for index in range(rows_count)
    ]


def _balanced_chunks(values: list[int], chunks_count: int) -> list[list[int]]:
    base_size, remainder = divmod(len(values), chunks_count)
    chunks: list[list[int]] = []
    start = 0
    for index in range(chunks_count):
        size = base_size + (1 if index < remainder else 0)
        chunks.append(values[start : start + size])
        start += size
    return [chunk for chunk in chunks if chunk]


def _create_model(model_name: str):
    if model_name == "random_forest":
        return create_random_forest_model()
    if model_name == "logistic_regression":
        return create_logistic_regression_model()
    if model_name == "extra_trees":
        return create_extra_trees_model()
    raise ValueError(f"Unsupported walk-forward model: {model_name}")


def _fit_model(model, x: list[list[float]], y: list[int], weights: list[float]) -> None:
    if hasattr(model, "named_steps"):
        model.fit(x, y, classifier__sample_weight=weights)
    else:
        model.fit(x, y, sample_weight=weights)


def _positive_probabilities(model, x: list[list[float]]) -> list[float]:
    return [float(item[1]) for item in model.predict_proba(x)]


def _metrics_with_calibration(labels: list[int], probabilities: list[float]) -> dict[str, float | None]:
    metrics = calculate_classification_metrics(labels, probabilities)
    metrics["calibration_error"] = build_calibration_report(labels, probabilities).get(
        "calibration_error"
    )
    metrics["sample_size"] = len(labels)
    return metrics


def _get_active_model(db: Session) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.promoted_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


def _resolve_model_name(requested: str, active_model: ModelVersion | None) -> str:
    if requested != "auto":
        return requested
    if active_model and active_model.model_name in {
        "random_forest",
        "logistic_regression",
        "extra_trees",
    }:
        return active_model.model_name
    return "random_forest"


def _write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run expanding-window validation on chronological real match data."
    )
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--min-train-rows", type=int, default=DEFAULT_MIN_TRAIN_ROWS)
    parser.add_argument("--min-eval-rows", type=int, default=DEFAULT_MIN_EVAL_ROWS)
    parser.add_argument(
        "--model",
        choices=["auto", "random_forest", "logistic_regression", "extra_trees"],
        default="auto",
    )
    parser.add_argument(
        "--training-profile",
        choices=["tier1_only", "tier1_plus_verified_pro"],
        default="tier1_plus_verified_pro",
    )
    parser.add_argument("--feature-set", choices=["all", "differential"], default="differential")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()
    try:
        report = run_walk_forward_validation(
            folds_count=args.folds,
            min_train_rows=args.min_train_rows,
            min_eval_rows=args.min_eval_rows,
            model_name=args.model,
            training_profile=args.training_profile,
            feature_set=args.feature_set,
            output_path=Path(args.output),
        )
    except (NotEnoughTrainingDataError, ValueError) as exc:
        print(f"Walk-forward validation unavailable: {exc}")
        return

    gate = report["stability_gate"]
    print("WALK-FORWARD VALIDATION")
    print(f"Status: {report['status']}")
    print(f"Model: {report['model_name']}")
    print(f"Valid folds: {gate['valid_folds']}")
    print(f"Tier 1 evaluation rows: {gate['total_tier1_evaluation_rows']}")
    print(f"Stability gate: {'passed' if gate['passed'] else 'failed'}")
    for reason in gate["reasons"]:
        print(f"- {reason}")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
