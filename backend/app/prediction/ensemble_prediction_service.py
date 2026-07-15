from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Backtest, Match, TeamRating
from app.patches.patch_service import calculate_days_since_patch
from app.rosters.roster_service import get_active_roster, has_recent_roster_change
from app.prediction.confidence_guard import apply_confidence_guard
from app.prediction.engine import FormulaPredictionEngine
from app.prediction.ensemble_weighting import (
    build_weighting_decision_for_db,
    filter_and_normalize_weights,
)
from app.prediction.ml_prediction_service import MLPredictionUnavailable, try_predict_with_ml
from app.prediction.schemas import EnsembleComponent, FormulaPredictionResponse, PredictionFactors
from app.ratings.rating_service import RATING_TYPE
from app.ratings.team_identity import resolve_scoped_team_identity_ids
from app.prediction.feature_snapshot import TIER1_HISTORY_SCOPE


ENSEMBLE_MODEL_VERSION = "ensemble_v1"
ENSEMBLE_WARNING = "Final prediction combines formula, Elo and local ML signals."
DISAGREEMENT_WARNING = "Prediction components disagree, confidence reduced."

DISAGREEMENT_THRESHOLD = 0.18
AGREEMENT_THRESHOLD = 0.08
MIN_PROBABILITY = 0.20
MAX_PROBABILITY = 0.80


@dataclass(frozen=True)
class EnsemblePredictionUnavailable:
    reason: str
    formula_prediction: FormulaPredictionResponse
    ml_result: FormulaPredictionResponse | MLPredictionUnavailable


def try_predict_with_ensemble(
    db: Session,
    match: Match,
    *,
    formula_history_scope: str = TIER1_HISTORY_SCOPE,
    allow_verified_pro_ml: bool = False,
) -> FormulaPredictionResponse | EnsemblePredictionUnavailable:
    formula_prediction = FormulaPredictionEngine().predict(
        db,
        match,
        history_scope=formula_history_scope,
    )
    elo_probability = _elo_probability(
        db,
        match,
        formula_prediction=formula_prediction,
    )
    ml_result = try_predict_with_ml(
        db,
        match,
        allow_verified_pro_inference=allow_verified_pro_ml,
    )

    available_probabilities: dict[str, float] = {
        "formula": formula_prediction.team_a_probability,
    }
    if elo_probability is not None:
        available_probabilities["elo"] = elo_probability
    if isinstance(ml_result, FormulaPredictionResponse):
        available_probabilities["ml"] = ml_result.team_a_probability

    if len(available_probabilities) < 2:
        return EnsemblePredictionUnavailable(
            reason="not_enough_components",
            formula_prediction=formula_prediction,
            ml_result=ml_result,
        )

    latest_backtest = _latest_backtest(db)
    weighting_decision = build_weighting_decision_for_db(db)
    weights = filter_and_normalize_weights(weighting_decision.weights, available_probabilities.keys())
    weighted_probability = sum(available_probabilities[name] * weights[name] for name in available_probabilities)
    disagreement = _max_disagreement(available_probabilities.values())
    if disagreement >= DISAGREEMENT_THRESHOLD:
        weighted_probability = 0.5 + (weighted_probability - 0.5) * 0.65
    team_a_probability, team_b_probability = _normalize_probability(weighted_probability)
    confidence_score = _confidence_score(
        component_count=len(available_probabilities),
        disagreement=disagreement,
        latest_backtest=latest_backtest,
        ml_result=ml_result,
    )
    confidence = _confidence_label(
        component_count=len(available_probabilities),
        disagreement=disagreement,
        latest_backtest=latest_backtest,
        ml_result=ml_result,
        confidence_score=confidence_score,
    )
    warning = _response_warning(
        base_warning=DISAGREEMENT_WARNING if disagreement >= DISAGREEMENT_THRESHOLD else ENSEMBLE_WARNING,
        weighting_warning=weighting_decision.warning,
    )
    component_summary = _component_summary(match, available_probabilities)

    response = FormulaPredictionResponse(
        match_id=str(match.id),
        prediction_type="ensemble",
        model_version=ENSEMBLE_MODEL_VERSION,
        team_a_probability=team_a_probability,
        team_b_probability=team_b_probability,
        confidence=confidence,
        confidence_score=confidence_score,
        factors=formula_prediction.factors,
        explanation=_build_explanation(
            component_summary=component_summary,
            probabilities=available_probabilities,
            weights=weights,
            match=match,
            disagreement=disagreement,
            ml_result=ml_result,
        ),
        warning=warning,
        fallback_used=False,
        fallback_reason=None,
        data_freshness=_data_freshness(ml_result),
        components=_components(
            probabilities=available_probabilities,
            weights=weights,
            ml_result=ml_result,
            elo_available=elo_probability is not None,
        ),
        weights=weights,
        component_summary=component_summary,
        weight_source=weighting_decision.weight_source,
        weight_reason=weighting_decision.weight_reason,
        backtest_metrics_used=weighting_decision.backtest_metrics_used,
        walk_forward_metrics_used=weighting_decision.walk_forward_metrics_used,
        analytics_context=formula_prediction.analytics_context,
    )
    guarded = apply_confidence_guard(
        response,
        context=_guard_context(db, match),
        latest_backtest=latest_backtest,
    )
    return guarded.prediction


def _elo_probability(
    db: Session,
    match: Match,
    *,
    formula_prediction: FormulaPredictionResponse | None = None,
) -> float | None:
    rating_a = _latest_elo_rating(db, match.team_a_id)
    rating_b = _latest_elo_rating(db, match.team_b_id)
    if rating_a is not None and rating_b is not None:
        rating_a_value = rating_a.rating_value
        rating_b_value = rating_b.rating_value
        matches_a = rating_a.matches_count
        matches_b = rating_b.matches_count
    else:
        analytics_context = (formula_prediction.analytics_context or {}) if formula_prediction else {}
        team_a_context = analytics_context.get("team_a") or {}
        team_b_context = analytics_context.get("team_b") or {}
        rating_a_value = team_a_context.get("elo_rating")
        rating_b_value = team_b_context.get("elo_rating")
        matches_a = int(team_a_context.get("matches_count") or 0)
        matches_b = int(team_b_context.get("matches_count") or 0)
        if rating_a_value is None or rating_b_value is None:
            return None
    raw_probability = 1 / (1 + 10 ** (-(float(rating_a_value) - float(rating_b_value)) / 400))
    data_score = min(matches_a, matches_b, 20) / 20
    probability = 0.5 + (raw_probability - 0.5) * (0.55 + data_score * 0.45)
    return round(max(MIN_PROBABILITY, min(MAX_PROBABILITY, probability)), 4)


def _latest_elo_rating(db: Session, team_id: int) -> TeamRating | None:
    identity_ids = resolve_scoped_team_identity_ids(db, team_id)
    return db.scalar(
        select(TeamRating)
        .where(
            TeamRating.team_id.in_(identity_ids),
            TeamRating.rating_type == RATING_TYPE,
        )
        .order_by(
            TeamRating.calculated_at.desc(),
            TeamRating.matches_count.desc(),
            TeamRating.id.desc(),
        )
        .limit(1)
    )


def _latest_backtest(db: Session) -> Backtest | None:
    return db.scalar(select(Backtest).order_by(Backtest.started_at.desc(), Backtest.id.desc()).limit(1))


def _response_warning(base_warning: str, weighting_warning: str | None) -> str:
    if not weighting_warning:
        return base_warning
    return f"{base_warning} {weighting_warning}"


def _normalize_probability(team_a_probability: float) -> tuple[float, float]:
    team_a = max(MIN_PROBABILITY, min(MAX_PROBABILITY, float(team_a_probability)))
    team_b = round(1.0 - team_a, 4)
    team_a = round(1.0 - team_b, 4)
    return team_a, team_b


def _max_disagreement(probabilities) -> float:
    values = list(probabilities)
    if len(values) < 2:
        return 0.0
    return max(values) - min(values)


def _confidence_score(
    *,
    component_count: int,
    disagreement: float,
    latest_backtest: Backtest | None,
    ml_result: FormulaPredictionResponse | MLPredictionUnavailable,
) -> float:
    score = 0.32 + component_count * 0.12
    if latest_backtest is not None:
        score += 0.12
    if disagreement <= AGREEMENT_THRESHOLD:
        score += 0.12
    if disagreement >= DISAGREEMENT_THRESHOLD:
        score -= 0.25
    if isinstance(ml_result, MLPredictionUnavailable):
        score -= 0.08
    if not _calibration_is_normal(latest_backtest):
        score -= 0.08
    return round(max(0.2, min(0.85, score)), 2)


def _confidence_label(
    *,
    component_count: int,
    disagreement: float,
    latest_backtest: Backtest | None,
    ml_result: FormulaPredictionResponse | MLPredictionUnavailable,
    confidence_score: float,
) -> str:
    if (
        component_count == 3
        and disagreement <= AGREEMENT_THRESHOLD
        and latest_backtest is not None
        and _calibration_is_normal(latest_backtest)
        and not isinstance(ml_result, MLPredictionUnavailable)
        and confidence_score >= 0.70
    ):
        return "high"
    if (
        component_count >= 2
        and disagreement < DISAGREEMENT_THRESHOLD
        and latest_backtest is not None
        and not isinstance(ml_result, MLPredictionUnavailable)
    ):
        return "medium"
    return "low"


def _calibration_is_normal(latest_backtest: Backtest | None) -> bool:
    if latest_backtest is None:
        return False
    metrics = latest_backtest.metrics_json or {}
    calibration = metrics.get("calibration") or {}
    values = [
        (calibration.get(name) or {}).get("calibration_error")
        for name in ("formula", "elo", "ml")
        if (calibration.get(name) or {}).get("calibration_error") is not None
    ]
    if not values:
        return True
    return max(float(value) for value in values) <= 0.18


def _component_summary(match: Match, probabilities: dict[str, float]) -> list[str]:
    labels = {"formula": "Formula", "elo": "Elo", "ml": "ML"}
    return [
        f"{labels[name]} favors {_favored_team(match, probability)} at {round(probability * 100)}%."
        for name, probability in probabilities.items()
    ]


def _favored_team(match: Match, team_a_probability: float) -> str:
    if math.isclose(team_a_probability, 0.5, abs_tol=0.005):
        return "neither team"
    return match.team_a.name if team_a_probability > 0.5 else match.team_b.name


def _build_explanation(
    *,
    component_summary: list[str],
    probabilities: dict[str, float],
    weights: dict[str, float],
    match: Match,
    disagreement: float,
    ml_result: FormulaPredictionResponse | MLPredictionUnavailable,
) -> dict[str, Any]:
    positive_factors = []
    negative_factors = []
    for name, probability in probabilities.items():
        factor = {
            "factor": f"{name}_component",
            "impact": round((probability - 0.5) * weights[name], 4),
            "text": component_summary[list(probabilities).index(name)],
        }
        if probability >= 0.5:
            positive_factors.append(factor)
        else:
            negative_factors.append(factor)

    if isinstance(ml_result, FormulaPredictionResponse) and isinstance(ml_result.explanation, dict):
        positive_factors.extend((ml_result.explanation.get("positive_factors") or [])[:2])
        negative_factors.extend((ml_result.explanation.get("negative_factors") or [])[:2])

    if disagreement >= DISAGREEMENT_THRESHOLD:
        negative_factors.append(
            {
                "factor": "component_disagreement",
                "impact": round(-disagreement, 4),
                "text": "Prediction components disagree, so confidence is reduced.",
            }
        )

    return {
        "summary": "Final prediction combines formula, Elo and ML signals."
        if "ml" in probabilities
        else "Final prediction combines formula and Elo signals.",
        "positive_factors": positive_factors,
        "negative_factors": negative_factors,
        "component_summary": component_summary,
        "raw_feature_values": {
            "weights": weights,
            "component_probabilities": probabilities,
            "team_a": match.team_a.name,
            "team_b": match.team_b.name,
        },
    }


def _components(
    *,
    probabilities: dict[str, float],
    weights: dict[str, float],
    ml_result: FormulaPredictionResponse | MLPredictionUnavailable,
    elo_available: bool,
) -> dict[str, EnsembleComponent]:
    return {
        "formula": EnsembleComponent(
            available=True,
            team_a_probability=probabilities.get("formula"),
            weight=weights.get("formula", 0.0),
        ),
        "elo": EnsembleComponent(
            available=elo_available,
            team_a_probability=probabilities.get("elo"),
            weight=weights.get("elo", 0.0),
            unavailable_reason=None if elo_available else "elo_rating_not_found",
        ),
        "ml": EnsembleComponent(
            available="ml" in probabilities,
            team_a_probability=probabilities.get("ml"),
            weight=weights.get("ml", 0.0),
            model_version=ml_result.model_version if isinstance(ml_result, FormulaPredictionResponse) else None,
            unavailable_reason=None if isinstance(ml_result, FormulaPredictionResponse) else ml_result.reason,
        ),
    }


def _data_freshness(ml_result: FormulaPredictionResponse | MLPredictionUnavailable) -> dict[str, str | None] | None:
    if isinstance(ml_result, FormulaPredictionResponse):
        return ml_result.data_freshness
    return None


def _guard_context(db: Session, match: Match) -> dict[str, Any]:
    return {
        "days_since_patch": calculate_days_since_patch(db, match.start_time),
        "teams": {
            "team_a": {
                "has_recent_roster_change": has_recent_roster_change(db, match.team_a_id, match.start_time),
                "roster_known": len(get_active_roster(db, match.team_a_id, match.start_time)) == 5,
            },
            "team_b": {
                "has_recent_roster_change": has_recent_roster_change(db, match.team_b_id, match.start_time),
                "roster_known": len(get_active_roster(db, match.team_b_id, match.start_time)) == 5,
            },
        },
    }
