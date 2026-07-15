from __future__ import annotations

import argparse
import json
import os
import statistics
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

from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier

from app.database import SessionLocal
from ml.config import ML_ARTIFACT_DIR, ML_RANDOM_STATE
from ml.evaluation.walk_forward import (
    DEFAULT_FOLDS,
    DEFAULT_MIN_EVAL_ROWS,
    DEFAULT_MIN_TRAIN_ROWS,
    _fit_model,
    _metrics_with_calibration,
    _positive_probabilities,
    build_walk_forward_folds,
)
from ml.models.calibration import calibrate_probabilities_guarded
from ml.models.logistic_regression_model import create_logistic_regression_model
from ml.training.dataset_builder import NotEnoughTrainingDataError, build_training_dataset


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "model_tournament_report.json"


def model_profiles() -> dict[str, dict[str, Any]]:
    return {
        "random_forest_current": {
            "model_type": "random_forest",
            "params": {
                "n_estimators": 200,
                "max_depth": 6,
                "min_samples_leaf": 1,
                "max_features": "sqrt",
            },
        },
        "random_forest_regularized": {
            "model_type": "random_forest",
            "params": {
                "n_estimators": 500,
                "max_depth": 5,
                "min_samples_leaf": 5,
                "max_features": "sqrt",
                "class_weight": "balanced",
            },
        },
        "extra_trees_regularized": {
            "model_type": "extra_trees",
            "params": {
                "n_estimators": 500,
                "max_depth": 7,
                "min_samples_leaf": 4,
                "max_features": "sqrt",
                "class_weight": "balanced",
            },
        },
        "hist_gradient_boosting": {
            "model_type": "hist_gradient_boosting",
            "params": {
                "max_iter": 250,
                "learning_rate": 0.04,
                "max_leaf_nodes": 15,
                "min_samples_leaf": 15,
                "l2_regularization": 1.0,
            },
        },
        "logistic_regression": {
            "model_type": "logistic_regression",
            "params": {},
        },
    }


def run_model_tournament(
    *,
    folds_count: int = DEFAULT_FOLDS,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    min_eval_rows: int = DEFAULT_MIN_EVAL_ROWS,
    output_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        dataset = build_training_dataset(
            db,
            min_rows=min_train_rows + min_eval_rows,
            training_profile="tier1_plus_verified_pro",
            feature_set="differential",
        )
        folds = build_walk_forward_folds(
            dataset.rows,
            folds_count=folds_count,
            min_train_rows=min_train_rows,
            min_eval_rows=min_eval_rows,
        )
        if len(folds) < 3:
            raise NotEnoughTrainingDataError("At least three chronological folds are required.")

        results: dict[str, Any] = {}
        for profile_name, profile in model_profiles().items():
            all_labels: list[int] = []
            all_probabilities: list[float] = []
            fold_results = []
            for fold in folds:
                train_indices = fold.train_indices
                calibration_rows = max(20, int(len(train_indices) * 0.15))
                core_indices = train_indices[:-calibration_rows]
                calibration_indices = train_indices[-calibration_rows:]
                model = create_profile_model(profile_name)
                _fit_model(
                    model,
                    [dataset.x[index] for index in core_indices],
                    [dataset.y[index] for index in core_indices],
                    [dataset.sample_weights[index] for index in core_indices],
                )
                calibration_probabilities = _positive_probabilities(
                    model,
                    [dataset.x[index] for index in calibration_indices],
                )
                calibrator, calibration_guard = calibrate_probabilities_guarded(
                    calibration_probabilities,
                    [dataset.y[index] for index in calibration_indices],
                )
                labels = [dataset.y[index] for index in fold.evaluation_indices]
                probabilities = _positive_probabilities(
                    model,
                    [dataset.x[index] for index in fold.evaluation_indices],
                )
                if calibrator is not None:
                    probabilities = calibrator.predict_proba(probabilities)
                metrics = _metrics_with_calibration(labels, probabilities)
                all_labels.extend(labels)
                all_probabilities.extend(probabilities)
                fold_results.append(
                    {
                        "fold": fold.number,
                        "evaluation_rows": len(labels),
                        "metrics": metrics,
                        "calibration_accepted": calibrator is not None,
                        "calibration_reason": calibration_guard["reason"],
                    }
                )

            aggregate = _metrics_with_calibration(all_labels, all_probabilities)
            fold_log_losses = [
                float(item["metrics"]["log_loss"])
                for item in fold_results
                if item["metrics"]["log_loss"] is not None
            ]
            stable = bool(
                aggregate["calibration_error"] is not None
                and aggregate["calibration_error"] <= 0.12
                and fold_log_losses
                and max(fold_log_losses) <= 0.75
            )
            results[profile_name] = {
                "model_type": profile["model_type"],
                "params": profile["params"],
                "aggregate_metrics": aggregate,
                "folds": fold_results,
                "worst_fold_log_loss": max(fold_log_losses) if fold_log_losses else None,
                "fold_log_loss_stddev": (
                    statistics.pstdev(fold_log_losses) if len(fold_log_losses) > 1 else 0.0
                ),
                "stable": stable,
            }

        recommended = choose_recommended_profile(results)
        report = {
            "status": "ok" if recommended else "warning",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_rows": len(dataset.rows),
            "tier1_evaluation_rows": sum(
                len(fold.evaluation_indices) for fold in folds
            ),
            "folds_count": len(folds),
            "feature_set": "differential",
            "training_profile": "tier1_plus_verified_pro",
            "results": results,
            "recommended_profile": recommended,
            "selection_rule": "stable lowest aggregate log_loss, then brier_score and fold variance",
            "active_model_changed": False,
            "warning": (
                "Metrics are preliminary and are not evidence of betting profitability."
            ),
        }
        _write_report(report, output_path)
        return report
    finally:
        db.close()


def choose_recommended_profile(results: dict[str, Any]) -> str | None:
    stable = [
        (name, result)
        for name, result in results.items()
        if result.get("stable")
        and result.get("aggregate_metrics", {}).get("log_loss") is not None
    ]
    if not stable:
        return None
    return min(
        stable,
        key=lambda item: (
            float(item[1]["aggregate_metrics"]["log_loss"]),
            float(item[1]["aggregate_metrics"]["brier_score"]),
            float(item[1]["fold_log_loss_stddev"]),
        ),
    )[0]


def create_profile_model(profile_name: str):
    profile = model_profiles().get(profile_name)
    if profile is None:
        raise ValueError(f"Unknown model profile: {profile_name}")
    model_type = profile["model_type"]
    params = profile["params"]
    if model_type == "logistic_regression":
        return create_logistic_regression_model()
    if model_type == "random_forest":
        return RandomForestClassifier(
            random_state=ML_RANDOM_STATE,
            n_jobs=1,
            **params,
        )
    if model_type == "extra_trees":
        return ExtraTreesClassifier(
            random_state=ML_RANDOM_STATE,
            n_jobs=1,
            **params,
        )
    if model_type == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(
            random_state=ML_RANDOM_STATE,
            **params,
        )
    raise ValueError(f"Unsupported model type: {model_type}")


def _write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare lightweight models on identical temporal folds.")
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--min-train-rows", type=int, default=DEFAULT_MIN_TRAIN_ROWS)
    parser.add_argument("--min-eval-rows", type=int, default=DEFAULT_MIN_EVAL_ROWS)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()
    try:
        report = run_model_tournament(
            folds_count=args.folds,
            min_train_rows=args.min_train_rows,
            min_eval_rows=args.min_eval_rows,
            output_path=Path(args.output),
        )
    except (NotEnoughTrainingDataError, ValueError) as exc:
        print(f"Model tournament unavailable: {exc}")
        return
    print("MODEL TOURNAMENT")
    print(f"Status: {report['status']}")
    print(f"Recommended profile: {report['recommended_profile']}")
    for name, result in report["results"].items():
        metrics = result["aggregate_metrics"]
        print(
            f"- {name}: log_loss={metrics['log_loss']:.4f}, "
            f"brier={metrics['brier_score']:.4f}, "
            f"auc={metrics['roc_auc']:.4f}, stable={result['stable']}"
        )
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
