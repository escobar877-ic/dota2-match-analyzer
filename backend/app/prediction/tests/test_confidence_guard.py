from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Backtest
from app.prediction.confidence_guard import (
    apply_confidence_guard,
    clamp_overconfident_probability,
    detect_component_disagreement,
)
from app.prediction.schemas import EnsembleComponent, FormulaPredictionResponse, PredictionFactors


class ConfidenceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_overconfident_low_confidence_prediction_moves_closer_to_50_50(self):
        self.assertLess(clamp_overconfident_probability(0.78, 0.30), 0.78)
        self.assertGreater(clamp_overconfident_probability(0.78, 0.30), 0.5)

    def test_components_disagreement_detected(self):
        components = {
            "formula": EnsembleComponent(available=True, team_a_probability=0.55, weight=0.5),
            "ml": EnsembleComponent(available=True, team_a_probability=0.76, weight=0.5),
        }

        self.assertTrue(detect_component_disagreement(components))

    def test_no_backtest_caps_confidence(self):
        prediction = self._prediction(confidence="high", confidence_score=0.82)
        result = apply_confidence_guard(prediction, latest_backtest=None)

        self.assertEqual(result.prediction.confidence, "medium")
        self.assertIn("No recent backtest is available.", result.reasons)

    def test_high_calibration_error_lowers_confidence(self):
        prediction = self._prediction(confidence="medium", confidence_score=0.52)
        backtest = self._backtest(calibration_error=0.25)
        result = apply_confidence_guard(prediction, latest_backtest=backtest)

        self.assertEqual(result.prediction.confidence, "low")
        self.assertIn("Backtest calibration error is high.", result.reasons)

    def test_recent_roster_change_lowers_confidence(self):
        prediction = self._prediction(confidence="medium", confidence_score=0.50)
        result = apply_confidence_guard(
            prediction,
            context={"teams": {"team_a": {"has_recent_roster_change": True}, "team_b": {}}},
            latest_backtest=self._backtest(),
        )

        self.assertEqual(result.prediction.confidence, "low")
        self.assertIn("Recent roster change detected.", result.reasons)

    def test_new_patch_lowers_confidence(self):
        prediction = self._prediction(confidence="medium", confidence_score=0.50)
        result = apply_confidence_guard(
            prediction,
            context={"days_since_patch": 3, "teams": {"team_a": {}, "team_b": {}}},
            latest_backtest=self._backtest(),
        )

        self.assertEqual(result.prediction.confidence, "low")
        self.assertIn("Current patch is very new.", result.reasons)

    def test_missing_roster_context_caps_high_confidence(self):
        prediction = self._prediction(confidence="high", confidence_score=0.78)
        result = apply_confidence_guard(
            prediction,
            context={
                "teams": {
                    "team_a": {"roster_known": False},
                    "team_b": {"roster_known": True},
                }
            },
            latest_backtest=self._backtest(),
        )

        self.assertEqual(result.prediction.confidence, "medium")
        self.assertIn("Current roster data is incomplete.", result.reasons)

    def test_probabilities_still_sum_to_one(self):
        prediction = self._prediction(confidence="low", confidence_score=0.30, probability=0.78)
        result = apply_confidence_guard(prediction, latest_backtest=None)

        self.assertAlmostEqual(result.prediction.team_a_probability + result.prediction.team_b_probability, 1.0, places=4)
        self.assertTrue(result.prediction.confidence_guard_applied)
        self.assertEqual(result.prediction.original_probability_before_guard, 0.78)

    def _prediction(
        self,
        *,
        confidence: str = "medium",
        confidence_score: float = 0.60,
        probability: float = 0.62,
    ) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id="1",
            prediction_type="ensemble",
            model_version="ensemble_v1",
            team_a_probability=probability,
            team_b_probability=round(1 - probability, 4),
            confidence=confidence,
            confidence_score=confidence_score,
            factors=PredictionFactors(
                recent_form=0,
                team_rating=0,
                head_to_head=0,
                hero_pool=0,
                roster_stability=0,
            ),
            explanation={"summary": "test"},
            warning="test",
            components={
                "formula": EnsembleComponent(available=True, team_a_probability=0.60, weight=0.5),
                "ml": EnsembleComponent(available=True, team_a_probability=0.64, weight=0.5),
            },
        )

    def _backtest(self, *, calibration_error: float = 0.08) -> Backtest:
        return Backtest(
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            dataset_type="real",
            matches_count=30,
            report_path="test",
            metrics_json={
                "calibration": {
                    "formula": {"calibration_error": calibration_error},
                    "elo": {"calibration_error": calibration_error},
                    "ml": {"calibration_error": calibration_error},
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
