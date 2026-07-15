from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.models import Backtest
from app.prediction.schemas import EnsembleComponent, FormulaPredictionResponse


DISAGREEMENT_THRESHOLD = 0.18
HIGH_CALIBRATION_ERROR = 0.18


@dataclass(frozen=True)
class GuardResult:
    prediction: FormulaPredictionResponse
    applied: bool
    reasons: list[str]
    original_probability: float | None


def clamp_overconfident_probability(probability: float, confidence_score: float) -> float:
    if confidence_score >= 0.45:
        return _bounded_probability(probability)
    shrink = 0.55 if confidence_score >= 0.30 else 0.40
    return _bounded_probability(0.5 + (probability - 0.5) * shrink)


def detect_component_disagreement(components: dict[str, EnsembleComponent] | None) -> bool:
    if not components:
        return False
    values = [
        component.team_a_probability
        for component in components.values()
        if component.available and component.team_a_probability is not None
    ]
    if len(values) < 2:
        return False
    return max(values) - min(values) >= DISAGREEMENT_THRESHOLD


def calculate_data_quality_penalty(context: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not context:
        return 0.0, []
    penalty = 0.0
    reasons = []
    teams = context.get("teams") or {}
    recent_roster_change = any(
        bool((teams.get(side) or {}).get("has_recent_roster_change")) for side in ("team_a", "team_b")
    )
    missing_roster_context = any(
        (teams.get(side) or {}).get("roster_known") is False for side in ("team_a", "team_b")
    )
    if recent_roster_change:
        penalty += 0.12
        reasons.append("Recent roster change detected.")
    if missing_roster_context:
        penalty += 0.08
        reasons.append("Current roster data is incomplete.")
    days_since_patch = context.get("days_since_patch")
    if isinstance(days_since_patch, (int, float)) and days_since_patch < 7:
        penalty += 0.10
        reasons.append("Current patch is very new.")
    return penalty, reasons


def calculate_calibration_penalty(latest_backtest: Backtest | None) -> tuple[float, list[str]]:
    if latest_backtest is None:
        return 0.10, ["No recent backtest is available."]
    metrics = latest_backtest.metrics_json or {}
    calibration = metrics.get("calibration") or {}
    errors = [
        float((calibration.get(name) or {}).get("calibration_error"))
        for name in ("formula", "elo", "ml")
        if (calibration.get(name) or {}).get("calibration_error") is not None
    ]
    if not errors:
        return 0.08, ["Backtest calibration data is unavailable."]
    if max(errors) > HIGH_CALIBRATION_ERROR:
        return 0.15, ["Backtest calibration error is high."]
    return 0.0, []


def apply_confidence_guard(
    prediction: FormulaPredictionResponse,
    context: dict[str, Any] | None = None,
    latest_backtest: Backtest | None = None,
) -> GuardResult:
    reasons = []
    original_probability = prediction.team_a_probability
    confidence_score = prediction.confidence_score
    confidence = prediction.confidence

    if detect_component_disagreement(prediction.components):
        reasons.append("Prediction components disagree.")
        confidence = "low"
        confidence_score = min(confidence_score, 0.35)

    calibration_penalty, calibration_reasons = calculate_calibration_penalty(latest_backtest)
    if calibration_reasons:
        reasons.extend(calibration_reasons)
    confidence_score -= calibration_penalty

    data_quality_penalty, data_quality_reasons = calculate_data_quality_penalty(context)
    if data_quality_reasons:
        reasons.extend(data_quality_reasons)
    confidence_score -= data_quality_penalty

    confidence_score = round(max(0.2, min(0.85, confidence_score)), 2)
    confidence = _lower_confidence(confidence, confidence_score)
    if "Current roster data is incomplete." in reasons and confidence == "high":
        confidence = "medium"
    if latest_backtest is None and confidence == "high":
        confidence = "medium"

    guarded_probability = prediction.team_a_probability
    if confidence == "low":
        guarded_probability = clamp_overconfident_probability(prediction.team_a_probability, confidence_score)

    probability_changed = abs(guarded_probability - prediction.team_a_probability) >= 0.0001
    prediction.team_a_probability, prediction.team_b_probability = _normalize_probabilities(guarded_probability)
    prediction.confidence = confidence
    prediction.confidence_score = confidence_score
    prediction.confidence_guard_applied = bool(reasons or probability_changed)
    prediction.confidence_reasons = _dedupe(reasons)
    prediction.original_probability_before_guard = round(original_probability, 4) if probability_changed else None
    return GuardResult(
        prediction=prediction,
        applied=prediction.confidence_guard_applied,
        reasons=prediction.confidence_reasons,
        original_probability=prediction.original_probability_before_guard,
    )


def _lower_confidence(current: str, confidence_score: float) -> str:
    if confidence_score < 0.45:
        return "low"
    if confidence_score < 0.70:
        return "medium"
    return current if current in {"medium", "high"} else "medium"


def _normalize_probabilities(team_a_probability: float) -> tuple[float, float]:
    team_a = _bounded_probability(team_a_probability)
    team_b = round(1.0 - team_a, 4)
    team_a = round(1.0 - team_b, 4)
    return team_a, team_b


def _bounded_probability(probability: float) -> float:
    return round(max(0.20, min(0.80, float(probability))), 4)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
