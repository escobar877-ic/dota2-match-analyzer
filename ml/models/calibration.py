from __future__ import annotations

from dataclasses import dataclass

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss


MIN_CALIBRATION_ROWS = 10


@dataclass
class ProbabilityCalibrator:
    model: LogisticRegression

    def predict_proba(self, probabilities: list[float]) -> list[float]:
        values = [[float(value)] for value in probabilities]
        return [float(row[1]) for row in self.model.predict_proba(values)]


def calibrate_probabilities(validation_probabilities: list[float], y_validation: list[int]) -> ProbabilityCalibrator | None:
    if len(validation_probabilities) < MIN_CALIBRATION_ROWS:
        return None
    if len(set(y_validation)) < 2:
        return None

    model = LogisticRegression(max_iter=200)
    model.fit([[float(value)] for value in validation_probabilities], y_validation)
    return ProbabilityCalibrator(model=model)


def calibrate_probabilities_guarded(
    validation_probabilities: list[float],
    y_validation: list[int],
) -> tuple[ProbabilityCalibrator | None, dict]:
    report = {
        "accepted": False,
        "reason": None,
        "fit_rows": 0,
        "guard_rows": 0,
        "raw_metrics": None,
        "calibrated_metrics": None,
    }
    if len(validation_probabilities) < MIN_CALIBRATION_ROWS * 2:
        report["reason"] = "not_enough_rows_for_temporal_calibration_guard"
        return None, report

    split_index = len(validation_probabilities) // 2
    fit_probabilities = validation_probabilities[:split_index]
    fit_labels = y_validation[:split_index]
    guard_probabilities = validation_probabilities[split_index:]
    guard_labels = y_validation[split_index:]
    report["fit_rows"] = len(fit_labels)
    report["guard_rows"] = len(guard_labels)
    if len(set(fit_labels)) < 2 or len(set(guard_labels)) < 2:
        report["reason"] = "calibration_split_has_only_one_target_class"
        return None, report

    calibrator = calibrate_probabilities(fit_probabilities, fit_labels)
    if calibrator is None:
        report["reason"] = "calibrator_fit_unavailable"
        return None, report

    calibrated_probabilities = calibrator.predict_proba(guard_probabilities)
    raw_metrics = _probability_metrics(guard_labels, guard_probabilities)
    calibrated_metrics = _probability_metrics(guard_labels, calibrated_probabilities)
    report["raw_metrics"] = raw_metrics
    report["calibrated_metrics"] = calibrated_metrics

    log_loss_safe = calibrated_metrics["log_loss"] <= raw_metrics["log_loss"] + 0.002
    brier_safe = calibrated_metrics["brier_score"] <= raw_metrics["brier_score"] + 0.001
    meaningful_gain = (
        calibrated_metrics["log_loss"] <= raw_metrics["log_loss"] - 0.005
        or calibrated_metrics["brier_score"] <= raw_metrics["brier_score"] - 0.003
    )
    if not (log_loss_safe and brier_safe and meaningful_gain):
        report["reason"] = "calibration_did_not_improve_temporal_guard_metrics"
        return None, report

    final_calibrator = calibrate_probabilities(validation_probabilities, y_validation)
    if final_calibrator is None:
        report["reason"] = "final_calibrator_fit_unavailable"
        return None, report
    report["accepted"] = True
    report["reason"] = "calibration_improved_temporal_guard_metrics"
    return final_calibrator, report


def _probability_metrics(labels: list[int], probabilities: list[float]) -> dict[str, float]:
    clipped = [min(0.999999, max(0.000001, float(value))) for value in probabilities]
    return {
        "log_loss": float(log_loss(labels, clipped, labels=[0, 1])),
        "brier_score": float(brier_score_loss(labels, clipped)),
    }
