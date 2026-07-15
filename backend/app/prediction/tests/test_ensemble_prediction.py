from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.matches import get_match_prediction
from app.database import Base
from app.db.models import Backtest, Match, ModelVersion, Team, TeamRating
from app.prediction.ensemble_prediction_service import _latest_elo_rating, try_predict_with_ensemble
from app.prediction.ml_prediction_service import MLPredictionUnavailable
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors


class EnsemblePredictionTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()

        self.tier1_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            status="upcoming",
            start_time=datetime(2026, 1, 10, tzinfo=timezone.utc),
            is_tier1_match=True,
        )
        self.excluded_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.lower.id,
            tournament_name="The International",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
        )
        self.db.add_all([self.tier1_match, self.excluded_match])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_ensemble_uses_formula_elo_and_ml_when_all_available(self):
        self._add_elo_ratings()
        self._add_backtest()

        with patch("app.prediction.ensemble_prediction_service.try_predict_with_ml", return_value=self._ml_prediction(0.62)):
            response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertTrue(response.components["formula"].available)
        self.assertTrue(response.components["elo"].available)
        self.assertTrue(response.components["ml"].available)
        self.assertEqual(response.components["ml"].model_version, "prematch_test")
        self.assertEqual(response.explanation["summary"], "Final prediction combines formula, Elo and ML signals.")
        self.assertIsInstance(response.confidence_guard_applied, bool)
        self.assertIsInstance(response.confidence_reasons, list)
        self.assertEqual(response.weight_source, "backtest")
        self.assertTrue(response.backtest_metrics_used)

    def test_weights_sum_to_one(self):
        self._add_elo_ratings()
        self._add_backtest()

        with patch("app.prediction.ensemble_prediction_service.try_predict_with_ml", return_value=self._ml_prediction(0.62)):
            response = try_predict_with_ensemble(self.db, self.tier1_match)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertAlmostEqual(sum(component.weight for component in response.components.values()), 1.0, places=4)
        self.assertAlmostEqual(sum(response.weights.values()), 1.0, places=4)

    def test_probabilities_sum_to_one(self):
        self._add_elo_ratings()

        with patch("app.prediction.ensemble_prediction_service.try_predict_with_ml", return_value=self._ml_prediction(0.62)):
            response = try_predict_with_ensemble(self.db, self.tier1_match)

        self.assertAlmostEqual(response.team_a_probability + response.team_b_probability, 1.0, places=4)

    def test_ml_unavailable_formula_and_elo_still_work(self):
        self._add_elo_ratings()

        with patch(
            "app.prediction.ensemble_prediction_service.try_predict_with_ml",
            return_value=MLPredictionUnavailable("model_artifacts_not_found"),
        ):
            response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertTrue(response.components["formula"].available)
        self.assertTrue(response.components["elo"].available)
        self.assertFalse(response.components["ml"].available)
        self.assertEqual(response.weight_source, "default")
        self.assertFalse(response.backtest_metrics_used)
        self.assertAlmostEqual(response.weights["formula"] + response.weights["elo"], 1.0, places=4)
        self.assertGreater(response.weights["formula"], response.weights["elo"])

    def test_only_formula_available_uses_existing_formula_fallback(self):
        response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertTrue(response.fallback_used)
        self.assertIsInstance(response.explanation, list)

    def test_non_tier1_still_returns_403(self):
        response = get_match_prediction(self.excluded_match.id, db=self.db)
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "This match is excluded from analysis because it is not Tier 1.")

    def test_disagreement_reduces_confidence(self):
        self._add_elo_ratings(rating_a=1500, rating_b=1500)
        formula = self._formula_prediction(0.55)

        with patch("app.prediction.ensemble_prediction_service.FormulaPredictionEngine") as engine_cls, patch(
            "app.prediction.ensemble_prediction_service.try_predict_with_ml",
            return_value=self._ml_prediction(0.75),
        ):
            engine_cls.return_value.predict.return_value = formula
            response = try_predict_with_ensemble(self.db, self.tier1_match)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertEqual(response.confidence, "low")
        self.assertEqual(response.warning, "Prediction components disagree, confidence reduced.")
        self.assertTrue(response.confidence_guard_applied)
        self.assertIn("Prediction components disagree.", response.confidence_reasons)
        self.assertIn("component_disagreement", {factor["factor"] for factor in response.explanation["negative_factors"]})

    def test_latest_backtest_reduces_ml_weight_if_ml_worse_than_formula(self):
        self._add_elo_ratings()
        self._add_backtest(
            formula_metrics={"log_loss": 0.45, "brier_score": 0.18},
            ml_metrics={"log_loss": 0.70, "brier_score": 0.28},
        )

        with patch("app.prediction.ensemble_prediction_service.try_predict_with_ml", return_value=self._ml_prediction(0.62)):
            response = try_predict_with_ensemble(self.db, self.tier1_match)

        self.assertLess(response.weights["ml"], 0.40)
        self.assertGreater(response.weights["formula"], 0.35)

    def test_elo_rating_resolves_same_real_team_across_sources(self):
        alias = Team(
            name="Spirit",
            external_source="csv_import",
            external_id="spirit-history",
        )
        self.team_b.external_source = "pandascore"
        self.db.add(alias)
        self.db.flush()
        self.db.add(
            TeamRating(
                team_id=alias.id,
                rating_type="elo",
                rating_value=1612,
                uncertainty=70,
                matches_count=30,
                calculated_at=datetime(2026, 1, 9, tzinfo=timezone.utc),
            )
        )
        self.db.commit()

        rating = _latest_elo_rating(self.db, self.team_b.id)

        self.assertIsNotNone(rating)
        self.assertEqual(rating.team_id, alias.id)
        self.assertEqual(rating.rating_value, 1612)

    def test_verified_history_formula_context_supplies_missing_persisted_elo(self):
        formula = self._formula_prediction(0.56)
        formula.analytics_context = {
            "team_a": {"elo_rating": 1650.0, "matches_count": 20},
            "team_b": {"elo_rating": 1550.0, "matches_count": 20},
        }

        with patch("app.prediction.ensemble_prediction_service.FormulaPredictionEngine") as engine_cls, patch(
            "app.prediction.ensemble_prediction_service.try_predict_with_ml",
            return_value=MLPredictionUnavailable("model_artifacts_not_found"),
        ):
            engine_cls.return_value.predict.return_value = formula
            response = try_predict_with_ensemble(self.db, self.tier1_match)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertTrue(response.components["elo"].available)
        self.assertGreater(response.components["elo"].team_a_probability, 0.5)

    def _add_elo_ratings(self, *, rating_a: float = 1560, rating_b: float = 1500) -> None:
        calculated_at = datetime(2026, 1, 9, tzinfo=timezone.utc)
        self.db.add_all(
            [
                TeamRating(
                    team_id=self.team_a.id,
                    rating_type="elo",
                    rating_value=rating_a,
                    uncertainty=80,
                    matches_count=12,
                    calculated_at=calculated_at,
                ),
                TeamRating(
                    team_id=self.team_b.id,
                    rating_type="elo",
                    rating_value=rating_b,
                    uncertainty=82,
                    matches_count=12,
                    calculated_at=calculated_at,
                ),
            ]
        )
        self.db.commit()

    def _add_backtest(
        self,
        *,
        formula_metrics: dict | None = None,
        ml_metrics: dict | None = None,
    ) -> None:
        model_version = ModelVersion(
            model_name="logistic_regression",
            model_type="sklearn",
            version="prematch_test",
            trained_at=datetime(2026, 1, 8, tzinfo=timezone.utc),
            artifact_path="temp",
            is_active=True,
        )
        self.db.add(model_version)
        self.db.flush()
        self.db.add(
            Backtest(
                model_version_id=model_version.id,
                started_at=datetime(2026, 1, 9, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 9, tzinfo=timezone.utc),
                dataset_type="real",
                matches_count=30,
                metrics_json={
                    "models": {
                        "formula": formula_metrics or {"log_loss": 0.58, "brier_score": 0.22},
                        "elo": {"log_loss": 0.60, "brier_score": 0.24},
                        "ml": ml_metrics or {"log_loss": 0.52, "brier_score": 0.20},
                    },
                    "calibration": {
                        "formula": {"calibration_error": 0.08},
                        "elo": {"calibration_error": 0.09},
                        "ml": {"calibration_error": 0.10},
                    },
                },
                report_path="ml/artifacts/backtest_report.json",
            )
        )
        self.db.commit()

    def _ml_prediction(self, probability: float) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id=str(self.tier1_match.id),
            prediction_type="ml",
            model_version="prematch_test",
            team_a_probability=probability,
            team_b_probability=round(1 - probability, 4),
            confidence="medium",
            confidence_score=0.5,
            factors=PredictionFactors(
                recent_form=0,
                team_rating=0,
                head_to_head=0,
                hero_pool=0,
                roster_stability=0,
            ),
            explanation={
                "summary": "Local ML explanation.",
                "positive_factors": [
                    {"factor": "elo_diff", "impact": 0.06, "text": "Team A has a stronger Elo rating."}
                ],
                "negative_factors": [],
                "raw_feature_values": {"elo_diff": 25.0},
            },
            warning="Local ML prediction. Formula/Elo remains available as fallback.",
            fallback_used=False,
            fallback_reason=None,
        )

    def _formula_prediction(self, probability: float) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id=str(self.tier1_match.id),
            prediction_type="formula",
            model_version="formula_v1",
            team_a_probability=probability,
            team_b_probability=round(1 - probability, 4),
            confidence="medium",
            confidence_score=0.5,
            factors=PredictionFactors(
                recent_form=0.02,
                team_rating=0.01,
                head_to_head=0,
                hero_pool=0,
                roster_stability=0,
            ),
            explanation=["Formula favors Team A."],
            warning="This is a probabilistic prediction, not a guaranteed result.",
        )


if __name__ == "__main__":
    unittest.main()
