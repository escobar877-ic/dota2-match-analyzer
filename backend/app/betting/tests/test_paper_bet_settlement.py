from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.betting.paper_bet_settlement import build_paper_bet_summary, settle_pending_paper_bets
from app.database import Base
from app.db.models import Match, PaperBet, Team


class PaperBetSettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team A", is_active_tier1=True)
        self.team_b = Team(name="Team B", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def test_settles_winning_series_bet(self):
        match = self._match(winner_team_id=self.team_a.id)
        bet = self._bet(match.id, outcome="team_a", odds=2.5)
        self.db.commit()

        report = settle_pending_paper_bets(self.db, artifact_path=None)

        self.db.refresh(bet)
        self.assertEqual(report["settled_now"], 1)
        self.assertEqual(bet.status, "won")
        self.assertEqual(bet.profit_units, 1.5)

    def test_settles_losing_series_bet(self):
        match = self._match(winner_team_id=self.team_b.id)
        bet = self._bet(match.id, outcome="team_a", odds=2.5)
        self.db.commit()

        settle_pending_paper_bets(self.db, artifact_path=None)

        self.db.refresh(bet)
        self.assertEqual(bet.status, "lost")
        self.assertEqual(bet.profit_units, -1.0)

    def test_settles_draw_series_bet(self):
        match = self._match(winner_team_id=None, is_draw=True, fmt="BO2")
        bet = self._bet(match.id, outcome="draw", odds=3.1)
        self.db.commit()

        settle_pending_paper_bets(self.db, artifact_path=None)

        self.db.refresh(bet)
        self.assertEqual(bet.status, "won")
        self.assertAlmostEqual(bet.profit_units, 2.1)

    def test_voids_map_winner_bet_when_series_draws(self):
        match = self._match(winner_team_id=None, is_draw=True, fmt="BO2")
        bet = self._bet(match.id, outcome="team_a", odds=2.0, market_type="map_winner")
        self.db.commit()

        report = settle_pending_paper_bets(self.db, artifact_path=None)

        self.db.refresh(bet)
        self.assertEqual(report["voided_now"], 1)
        self.assertEqual(bet.status, "void")
        self.assertEqual(bet.profit_units, 0.0)

    def test_dry_run_does_not_modify_bets(self):
        match = self._match(winner_team_id=self.team_a.id)
        bet = self._bet(match.id, outcome="team_a", odds=2.5)
        self.db.commit()

        report = settle_pending_paper_bets(self.db, dry_run=True, artifact_path=None)

        self.db.refresh(bet)
        self.assertEqual(report["settled_now"], 1)
        self.assertEqual(bet.status, "pending")
        self.assertIsNone(bet.profit_units)

    def test_summary_calculates_roi(self):
        match = self._match(winner_team_id=self.team_a.id)
        self._bet(match.id, outcome="team_a", odds=2.5)
        self._bet(match.id, outcome="team_b", odds=2.0)
        self.db.commit()
        settle_pending_paper_bets(self.db, artifact_path=None)

        summary = build_paper_bet_summary(self.db)

        self.assertEqual(summary["settled_bets"], 2)
        self.assertEqual(summary["won_bets"], 1)
        self.assertEqual(summary["lost_bets"], 1)
        self.assertEqual(summary["total_profit_units"], 0.5)
        self.assertEqual(summary["roi"], 0.25)

    def _match(self, *, winner_team_id: int | None, is_draw: bool = False, fmt: str = "BO3") -> Match:
        match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 7, 10, tzinfo=timezone.utc),
            format=fmt,
            status="finished",
            winner_team_id=winner_team_id,
            is_draw=is_draw,
            is_tier1_match=True,
        )
        self.db.add(match)
        self.db.flush()
        return match

    def _bet(
        self,
        match_id: int,
        *,
        outcome: str,
        odds: float,
        market_type: str = "series_result",
    ) -> PaperBet:
        bet = PaperBet(
            match_id=match_id,
            market_type=market_type,
            outcome=outcome,
            model_probability=0.5,
            decimal_odds=odds,
            no_vig_probability=0.45,
            edge=0.05,
            expected_value=0.1,
            stake_units=1.0,
            status="pending",
        )
        self.db.add(bet)
        self.db.flush()
        return bet


if __name__ == "__main__":
    unittest.main()
