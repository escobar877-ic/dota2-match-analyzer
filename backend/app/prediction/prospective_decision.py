from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.database import SessionLocal
from app.prediction.forecast_tracker import build_prospective_report
from ml.config import ML_ARTIFACT_DIR


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "prospective_decision_report.json"
MINIMUM_FINAL_FORECASTS = 100
RECOMMENDED_FINAL_FORECASTS = 300
MINIMUM_FINAL_CAPTURE_RATE = 0.95
COMPONENTS = ("ensemble", "formula", "elo", "ml")


def build_prospective_decision(prospective: dict[str, Any]) -> dict[str, Any]:
    final_settled = int(prospective.get("primary_settled_forecasts") or 0)
    coverage = prospective.get("coverage") or {}
    capture_rate = _number(coverage.get("final_capture_rate"))
    component_metrics = prospective.get("component_metrics") or {}
    minimum_component_rows = max(1, int(final_settled * 0.95)) if final_settled else 0
    component_samples = {
        component: int((component_metrics.get(component) or {}).get("sample_size") or 0)
        for component in COMPONENTS
    }

    sample_ready = final_settled >= MINIMUM_FINAL_FORECASTS
    capture_ready = capture_rate is not None and capture_rate >= MINIMUM_FINAL_CAPTURE_RATE
    components_ready = sample_ready and all(
        component_samples[component] >= minimum_component_rows for component in COMPONENTS
    )
    ready = sample_ready and capture_ready and components_ready

    best_log_loss = _best_component(component_metrics, "log_loss") if ready else None
    best_brier = _best_component(component_metrics, "brier_score") if ready else None
    reasons: list[str] = []
    if not sample_ready:
        reasons.append(
            f"Collect {max(0, MINIMUM_FINAL_FORECASTS - final_settled)} more primary final forecasts."
        )
    if not capture_ready:
        reasons.append("Final forecast capture must reach at least 95% on settled tracked matches.")
    if sample_ready and not components_ready:
        reasons.append("Component predictions are missing for too many primary final forecasts.")

    recommendation = _recommendation(best_log_loss, best_brier) if ready else "continue_collecting"
    if ready:
        reasons.extend(_review_reasons(best_log_loss, best_brier))

    return {
        "status": "ok",
        "decision_status": "review_required" if ready else "collecting",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strict_final_forecasts": final_settled,
        "minimum_final_forecasts": MINIMUM_FINAL_FORECASTS,
        "recommended_final_forecasts": RECOMMENDED_FINAL_FORECASTS,
        "remaining_to_minimum": max(0, MINIMUM_FINAL_FORECASTS - final_settled),
        "final_capture_rate": capture_rate,
        "minimum_final_capture_rate": MINIMUM_FINAL_CAPTURE_RATE,
        "component_samples": component_samples,
        "component_metrics": {
            component: _metric_summary(component_metrics.get(component) or {})
            for component in COMPONENTS
        },
        "best_by_log_loss": best_log_loss,
        "best_by_brier_score": best_brier,
        "recommended_action": recommendation,
        "reasons": reasons,
        "candidate_training_allowed": ready,
        "automatic_training_enabled": False,
        "promotion_allowed": False,
        "automatic_promotion_enabled": False,
        "betting_claims_allowed": False,
        "verified_pro_preview_used": False,
        "warning": (
            "Prospective quality is not ready for model decisions."
            if not ready
            else "Manual review and a leakage-free candidate backtest are still required before promotion."
        ),
    }


def refresh_prospective_decision(
    *,
    db_factory: Callable[[], Any] = SessionLocal,
    artifact_path: str | Path | None = REPORT_PATH,
) -> dict[str, Any]:
    db = db_factory()
    try:
        decision = build_prospective_decision(build_prospective_report(db))
    finally:
        db.close()
    _write_report(decision, artifact_path)
    return decision


def _best_component(metrics: dict[str, Any], metric: str) -> str | None:
    candidates = []
    for component in COMPONENTS:
        value = _number((metrics.get(component) or {}).get(metric))
        if value is not None:
            candidates.append((value, component))
    return min(candidates)[1] if candidates else None


def _recommendation(best_log_loss: str | None, best_brier: str | None) -> str:
    if best_log_loss == best_brier == "ensemble":
        return "keep_ensemble_and_continue_monitoring"
    if best_log_loss == best_brier == "formula":
        return "train_new_ml_candidate_and_review_formula_weight"
    if best_log_loss == best_brier == "ml":
        return "review_ml_weight_after_candidate_backtest"
    if best_log_loss == best_brier == "elo":
        return "review_rating_weight_and_retrain_candidate"
    return "manual_component_weight_review"


def _review_reasons(best_log_loss: str | None, best_brier: str | None) -> list[str]:
    if best_log_loss == best_brier:
        return [f"{best_log_loss or 'No component'} is best on both prospective scoring rules."]
    return [
        f"Prospective scoring rules disagree: log_loss favors {best_log_loss}, Brier favors {best_brier}."
    ]


def _metric_summary(metrics: dict[str, Any]) -> dict[str, float | int | None]:
    return {
        "sample_size": int(metrics.get("sample_size") or 0),
        "accuracy": _number(metrics.get("accuracy")),
        "log_loss": _number(metrics.get("log_loss")),
        "brier_score": _number(metrics.get("brier_score")),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)
