from __future__ import annotations

from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score


def calculate_classification_metrics(y_true: list[int], probabilities: list[float]) -> dict[str, float | None]:
    if not y_true:
        return {
            "accuracy": None,
            "log_loss": None,
            "brier_score": None,
            "roc_auc": None,
        }

    clipped = [min(0.999999, max(0.000001, float(value))) for value in probabilities]
    predictions = [1 if value >= 0.5 else 0 for value in clipped]
    metrics: dict[str, float | None] = {
        "accuracy": float(accuracy_score(y_true, predictions)),
        "log_loss": float(log_loss(y_true, clipped, labels=[0, 1])),
        "brier_score": float(brier_score_loss(y_true, clipped)),
        "roc_auc": None,
    }
    if len(set(y_true)) >= 2:
        metrics["roc_auc"] = float(roc_auc_score(y_true, clipped))
    return metrics
