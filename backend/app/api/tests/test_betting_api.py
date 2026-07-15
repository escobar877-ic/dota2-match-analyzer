from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.betting import evaluate_odds, paper_bets_summary
from app.betting.schemas import MarketEvaluationRequest
from app.database import Base
from app.db.models import MarketOddsSnapshot, Match, PaperBet, Team
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors


class BettingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        team_a = Team(name="Team A", is_active_tier1=True)
        team_b = Team(name="Team B", is_active_tier1=True)
        self.db.add_all([team_a, team_b])
        self.db.flush()
        self.match = Match(
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="The International",
            start_time=datetime.now(timezone.utc) + timedelta(days=1),
            format="BO2",
            status="upcoming",
            is_tier1_match=True,
        )
        self.db.add(self.match)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_evaluation_records_odds_and_idempotent_pending_paper_test(self):
        payload = MarketEvaluationRequest(
            bookmaker="test",
            team_a_odds=4.0,
            draw_odds=4.0,
            team_b_odds=4.0,
        )
        with patch("app.api.betting.get_match_prediction", return_value=self._prediction()):
            first = evaluate_odds(self.match.id, payload, self.db)
            second = evaluate_odds(self.match.id, payload, self.db)

        self.assertTrue(first.paper_test_eligible)
        self.assertEqual(first.paper_bet_id, second.paper_bet_id)
        self.assertEqual(self.db.query(PaperBet).count(), 1)
        self.assertEqual(self.db.query(MarketOddsSnapshot).count(), 6)

    def test_evaluation_rejects_match_after_start_without_writing_odds(self):
        self.match.status = "finished"
        self.match.start_time = datetime.now(timezone.utc) - timedelta(hours=2)
        self.db.commit()
        payload = MarketEvaluationRequest(
            bookmaker="test",
            team_a_odds=2.0,
            draw_odds=3.0,
            team_b_odds=2.0,
        )

        with self.assertRaises(HTTPException) as raised:
            evaluate_odds(self.match.id, payload, self.db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.db.query(PaperBet).count(), 0)
        self.assertEqual(self.db.query(MarketOddsSnapshot).count(), 0)

    def test_evaluation_rejects_capture_time_after_match_start(self):
        payload = MarketEvaluationRequest(
            bookmaker="test",
            team_a_odds=2.0,
            draw_odds=3.0,
            team_b_odds=2.0,
            captured_at=self.match.start_time + timedelta(minutes=1),
        )

        with patch("app.api.betting.get_match_prediction", return_value=self._prediction()):
            with self.assertRaises(HTTPException) as raised:
                evaluate_odds(self.match.id, payload, self.db)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.db.query(MarketOddsSnapshot).count(), 0)

    def test_paper_bets_summary_works(self):
        self.db.add(
            PaperBet(
                match_id=self.match.id,
                market_type="series_result",
                outcome="team_a",
                model_probability=0.6,
                decimal_odds=2.0,
                no_vig_probability=0.5,
                edge=0.1,
                expected_value=0.2,
                stake_units=1.0,
                status="won",
                profit_units=1.0,
            )
        )
        self.db.commit()

        response = paper_bets_summary(db=self.db)

        self.assertEqual(response["total_bets"], 1)
        self.assertEqual(response["won_bets"], 1)
        self.assertEqual(response["total_profit_units"], 1.0)

    def _prediction(self) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id=str(self.match.id),
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
