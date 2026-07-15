from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Backtest, ModelVersion
from ml.config import ML_ARTIFACT_DIR


MIN_WEIGHT = 0.10
MAX_WEIGHT = 0.65
WALK_FORWARD_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "walk_forward_report.json"


@dataclass(frozen=True)
class WeightingDecision:
    weights: dict[str, float]
    weight_source: str
    weight_reason: str
    backtest_metrics_used: bool
    warning: str | None = None
    walk_forward_metrics_used: bool = False


def get_default_weights() -> dict[str, float]:
    return {"formula": 0.35, "elo": 0.25, "ml": 0.40}


def get_latest_backtest_metrics(db: Session) -> dict[str, Any] | None:
    active_model = db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.promoted_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )
    statement = select(Backtest)
    if active_model is not None:
        statement = statement.where(Backtest.model_version_id == active_model.id)
    backtest = db.scalar(statement.order_by(Backtest.started_at.desc(), Backtest.id.desc()).limit(1))
    if backtest is None or not backtest.metrics_json:
        return None
    return {
        "model_version_id": backtest.model_version_id,
        "dataset_type": backtest.dataset_type,
        "matches_count": backtest.matches_count,
        "metrics_json": backtest.metrics_json,
    }


def get_walk_forward_weight_metrics(
    db: Session,
    *,
    report_path: str | Path = WALK_FORWARD_REPORT_PATH,
    max_age_days: int = 30,
) -> dict[str, Any] | None:
    path = Path(report_path)
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    active_model = db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.promoted_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )
    if active_model is None or report.get("active_model_version_id") != active_model.id:
        return None
    generated_at = _parse_datetime(report.get("generated_at"))
    if generated_at is None or generated_at < datetime.now(timezone.utc) - timedelta(days=max_age_days):
        return None
    latest_backtest_started_at = db.scalar(
        select(Backtest.started_at)
        .where(Backtest.model_version_id == active_model.id)
        .order_by(Backtest.started_at.desc(), Backtest.id.desc())
        .limit(1)
    )
    if latest_backtest_started_at is not None:
        if latest_backtest_started_at.tzinfo is None:
            latest_backtest_started_at = latest_backtest_started_at.replace(tzinfo=timezone.utc)
        if generated_at < latest_backtest_started_at:
            return None
    optimization = report.get("weight_optimization") or {}
    if not optimization.get("production_approved"):
        return None
    if not (report.get("stability_gate") or {}).get("passed"):
        return None
    if int(optimization.get("validation_rows") or 0) < 20:
        return None
    return {
        "weights": optimization.get("production_weights") or optimization.get("recommended_weights"),
        "generated_at": report.get("generated_at"),
        "active_model_version_id": active_model.id,
        "active_model_version": active_model.version,
        "validation_rows": optimization.get("validation_rows"),
        "selection_rows": optimization.get("selection_rows"),
        "method": optimization.get("method"),
        "production_approved": True,
    }


def calculate_weights_from_backtest(metrics: dict[str, Any] | None) -> dict[str, float]:
    weights = get_default_weights()
    if not metrics:
        return weights

    models = (metrics.get("metrics_json") or {}).get("models") or {}
    scores = {name: _quality_score(models.get(name) or {}) for name in weights}
    available_scores = {name: score for name, score in scores.items() if score is not None}
    if not available_scores:
        return weights

    formula_metrics = models.get("formula") or {}
    elo_metrics = models.get("elo") or {}
    ml_metrics = models.get("ml") or {}

    if _is_better(formula_metrics, ml_metrics):
        weights["formula"] += 0.12
        weights["ml"] -= 0.12
    if _is_better(elo_metrics, ml_metrics):
        weights["elo"] += 0.08
        weights["ml"] -= 0.08

    best_model = min(available_scores, key=lambda name: available_scores[name])
    if best_model == "formula":
        weights["formula"] += 0.08
        weights["ml"] -= 0.04
        weights["elo"] -= 0.04
    elif best_model == "elo":
        weights["elo"] += 0.08
        weights["formula"] -= 0.03
        weights["ml"] -= 0.05
    elif best_model == "ml":
        weights["ml"] += 0.10
        weights["formula"] -= 0.05
        weights["elo"] -= 0.05

    return normalize_weights(apply_weight_safety_limits(weights))


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    if not weights:
        return {}
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return _round_weights({name: 1 / len(weights) for name in weights})
    return _round_weights({name: max(0.0, value) / total for name, value in weights.items()})


def apply_weight_safety_limits(weights: dict[str, float]) -> dict[str, float]:
    if not weights:
        return {}
    adjusted = {name: min(MAX_WEIGHT, max(MIN_WEIGHT, value)) for name, value in weights.items()}
    for _ in range(12):
        total = sum(adjusted.values())
        delta = 1.0 - total
        if abs(delta) < 0.000001:
            break
        if delta > 0:
            candidates = {name: MAX_WEIGHT - value for name, value in adjusted.items() if value < MAX_WEIGHT}
        else:
            candidates = {name: value - MIN_WEIGHT for name, value in adjusted.items() if value > MIN_WEIGHT}
        capacity = sum(candidates.values())
        if capacity <= 0:
            break
        for name, room in candidates.items():
            adjusted[name] += delta * (room / capacity)
            adjusted[name] = min(MAX_WEIGHT, max(MIN_WEIGHT, adjusted[name]))
    return _round_weights(adjusted)


def build_weighting_decision(
    metrics: dict[str, Any] | None,
    walk_forward: dict[str, Any] | None = None,
) -> WeightingDecision:
    if walk_forward and walk_forward.get("production_approved"):
        candidate = walk_forward.get("weights") or {}
        if set(candidate) == {"formula", "elo", "ml"}:
            weights = normalize_weights(apply_weight_safety_limits(candidate))
            return WeightingDecision(
                weights=weights,
                weight_source="walk_forward",
                weight_reason=(
                    "Weights selected on earlier temporal folds and approved on the untouched latest fold "
                    f"({int(walk_forward.get('validation_rows') or 0)} Tier 1 matches)."
                ),
                backtest_metrics_used=False,
                walk_forward_metrics_used=True,
            )
    if not metrics:
        return WeightingDecision(
            weights=get_default_weights(),
            weight_source="default",
            weight_reason="No latest backtest available; using default ensemble weights.",
            backtest_metrics_used=False,
        )

    weights = calculate_weights_from_backtest(metrics)
    warning = None
    if metrics.get("dataset_type") == "dev_seed":
        warning = "Weights are based on synthetic dev seed backtest and are not real accuracy."
    return WeightingDecision(
        weights=weights,
        weight_source="backtest",
        weight_reason=_weight_reason(metrics, weights),
        backtest_metrics_used=True,
        warning=warning,
    )


def build_weighting_decision_for_db(db: Session) -> WeightingDecision:
    return build_weighting_decision(
        get_latest_backtest_metrics(db),
        get_walk_forward_weight_metrics(db),
    )


def filter_and_normalize_weights(weights: dict[str, float], component_names) -> dict[str, float]:
    selected = {name: weights[name] for name in component_names if name in weights}
    return normalize_weights(apply_weight_safety_limits(selected))


def _quality_score(metrics: dict[str, Any]) -> float | None:
    log_loss = metrics.get("log_loss")
    brier_score = metrics.get("brier_score")
    if log_loss is None or brier_score is None:
        return None
    return float(log_loss) + float(brier_score)


def _is_better(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
    candidate_log_loss = candidate.get("log_loss")
    candidate_brier = candidate.get("brier_score")
    other_log_loss = other.get("log_loss")
    other_brier = other.get("brier_score")
    if None in {candidate_log_loss, candidate_brier, other_log_loss, other_brier}:
        return False
    return float(candidate_log_loss) < float(other_log_loss) and float(candidate_brier) < float(other_brier)


def _weight_reason(metrics: dict[str, Any], weights: dict[str, float]) -> str:
    models = (metrics.get("metrics_json") or {}).get("models") or {}
    scores = {name: _quality_score(models.get(name) or {}) for name in weights}
    available = {name: score for name, score in scores.items() if score is not None}
    if not available:
        return "Latest backtest found, but model metrics were incomplete; using safe normalized weights."
    best_model = min(available, key=lambda name: available[name])
    return f"Latest backtest metrics favor {best_model}; ensemble weights adjusted within safety limits."


def _round_weights(weights: dict[str, float]) -> dict[str, float]:
    names = list(weights)
    rounded = {name: round(weights[name], 4) for name in names}
    if names:
        rounded[names[-1]] = round(1.0 - sum(rounded[name] for name in names[:-1]), 4)
    return rounded


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
