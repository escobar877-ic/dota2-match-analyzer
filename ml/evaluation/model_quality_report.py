from __future__ import annotations

from ml.evaluation.calibration_report import build_calibration_report
from ml.evaluation.metrics import calculate_classification_metrics
from ml.evaluation.monthly_report import build_monthly_report


DEV_SEED_WARNING = "Dev seed data is synthetic and must not be used for real accuracy claims."


def build_model_quality_report(records: list[dict], dataset_type: str, ml_available: bool) -> dict:
    models = {}
    for model_name in ["formula", "elo", "ml"]:
        probabilities = [record[model_name] for record in records if record.get(model_name) is not None]
        labels = [record["label"] for record in records if record.get(model_name) is not None]
        metrics = calculate_classification_metrics(labels, probabilities)
        calibration = build_calibration_report(labels, probabilities)
        metrics["calibration_error"] = calibration.get("calibration_error")
        models[model_name] = metrics

    available_models = {
        name: metrics
        for name, metrics in models.items()
        if metrics.get("log_loss") is not None
    }
    report = {
        "dataset_type": dataset_type,
        "warning": DEV_SEED_WARNING if dataset_type == "dev_seed" else None,
        "matches_count": len(records),
        "ml_available": ml_available,
        "models": models,
        "best_by_log_loss": _best_model(available_models, "log_loss"),
        "best_by_brier_score": _best_model(available_models, "brier_score"),
        "calibration": {
            name: build_calibration_report(
                [record["label"] for record in records if record.get(name) is not None],
                [record[name] for record in records if record.get(name) is not None],
            )
            for name in ["formula", "elo", "ml"]
        },
        "monthly": build_monthly_report(records),
    }
    return report


def _best_model(models: dict[str, dict], metric_name: str) -> str | None:
    candidates = [(name, metrics.get(metric_name)) for name, metrics in models.items()]
    candidates = [(name, value) for name, value in candidates if value is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1])[0]
