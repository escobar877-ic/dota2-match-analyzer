from __future__ import annotations

import unittest

from app.betting.schemas import MarketEvaluationRequest
from app.betting.value_service import evaluate_market
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors


class ValueServiceTests(unittest.TestCase):
    def test_removes_margin_and_calculates_edge(self):
        prediction = self._prediction()
        result = evaluate_market(
            prediction,
            MarketEvaluationRequest(
                team_a_odds=3.0,
                draw_odds=2.2,
                team_b_odds=3.2,
            ),
        )

        self.assertEqual(result["market_type"], "series_result")
        self.assertEqual(len(result["outcomes"]), 3)
        self.assertAlmostEqual(
            sum(item["no_vig_probability"] for item in result["outcomes"]),
            1.0,
            places=3,
        )

    def test_missing_draw_odds_rejected_for_bo2(self):
        with self.assertRaisesRegex(ValueError, "draw_odds"):
            evaluate_market(
                self._prediction(),
                MarketEvaluationRequest(team_a_odds=3.0, team_b_odds=3.2),
            )

    def test_incomplete_roster_blocks_paper_test(self):
        prediction = self._prediction()
        prediction.confidence_reasons = ["Current roster data is incomplete."]

        result = evaluate_market(
            prediction,
            MarketEvaluationRequest(
                team_a_odds=4.0,
                draw_odds=4.0,
                team_b_odds=4.0,
            ),
        )

        self.assertFalse(result["paper_test_eligible"])
        self.assertIn("Current roster data is incomplete.", result["guard_reasons"])

    def _prediction(self) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id="1",
            prediction_type="ensemble",
            model_version="ensemble_v1",
            team_a_probability=0.6,
            team_b_probability=0.4,
            confidence="medium",
            confidence_score=0.7,
            factors=PredictionFactors(
                recent_form=0,
                team_rating=0,
                head_to_head=0,
                hero_pool=0,
                roster_stability=0,
            ),
            explanation={"summary": "test"},
            warning="test",
            backtest_metrics_used=True,
            series_outcomes={
                "format": "BO2",
                "team_a_win": 0.36,
                "draw": 0.48,
                "team_b_win": 0.16,
            },
        )


if __name__ == "__main__":
    unittest.main()
