from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.db.models import Match, Player, Team, TeamRoster
from app.prediction.feature_snapshot import build_match_feature_snapshot


class FormulaTemporalGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def test_future_result_does_not_affect_formula_snapshot(self):
        target_time = datetime(2026, 1, 10, tzinfo=timezone.utc)
        past = self._match(target_time - timedelta(days=1), self.team_a.id)
        target = self._match(target_time, self.team_a.id, status="upcoming")
        future = self._match(target_time + timedelta(days=1), self.team_b.id)
        self.db.add_all([past, target, future])
        self.db.commit()

        snapshot = build_match_feature_snapshot(self.db, target)

        self.assertEqual(snapshot.team_a.matches_count, 1)
        self.assertEqual(snapshot.team_a.recent_form, 1.0)
        self.assertEqual(snapshot.head_to_head_count, 1)
        self.assertEqual(snapshot.head_to_head, 0.5)

    def test_formula_snapshot_uses_active_roster_not_all_saved_players(self):
        target_time = datetime(2026, 1, 10, tzinfo=timezone.utc)
        players = [
            Player(
                nickname=f"player-{index}",
                team_id=self.team_a.id,
                external_source="pandascore",
                external_id=f"player-{index}",
            )
            for index in range(7)
        ]
        self.db.add_all(players)
        self.db.flush()
        for index, player in enumerate(players):
            self.db.add(
                TeamRoster(
                    team_id=self.team_a.id,
                    player_id=player.id,
                    is_active=index < 5,
                    start_date=target_time - timedelta(days=30),
                    end_date=None if index < 5 else target_time - timedelta(days=1),
                    source="pandascore",
                )
            )
        target = self._match(target_time, self.team_a.id, status="upcoming")
        self.db.add(target)
        self.db.commit()

        snapshot = build_match_feature_snapshot(self.db, target)

        self.assertEqual(snapshot.team_a.roster_count, 5)
        self.assertEqual(snapshot.team_a.roster_stability, 1.0)

    def _match(self, start_time, winner_team_id, *, status="finished"):
        return Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            status=status,
            start_time=start_time,
            winner_team_id=winner_team_id if status == "finished" else None,
            is_tier1_match=True,
        )


if __name__ == "__main__":
    unittest.main()
