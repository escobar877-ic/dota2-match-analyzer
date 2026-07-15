from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Match
from app.prediction.engine import FormulaPredictionEngine
from app.prediction.ensemble_prediction_service import (
    EnsemblePredictionUnavailable,
    try_predict_with_ensemble,
)
from app.prediction.ml_prediction_service import MLPredictionUnavailable
from app.prediction.schemas import FormulaPredictionResponse
from app.prediction.series_outcomes import attach_series_outcomes


def build_match_prediction(db: Session, match: Match) -> FormulaPredictionResponse:
    ensemble_prediction = try_predict_with_ensemble(db, match)
    if not isinstance(ensemble_prediction, EnsemblePredictionUnavailable):
        return attach_series_outcomes(ensemble_prediction, match.format)

    ml_prediction = ensemble_prediction.ml_result
    if not isinstance(ml_prediction, MLPredictionUnavailable):
        return attach_series_outcomes(ml_prediction, match.format)

    formula_prediction = FormulaPredictionEngine().predict_and_save(
        db,
        match,
        fallback_used=True,
        fallback_reason=ml_prediction.reason,
    )
    return attach_series_outcomes(formula_prediction, match.format)
