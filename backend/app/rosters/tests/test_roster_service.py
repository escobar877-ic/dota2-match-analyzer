from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Match, Player, Team, TeamRoster
from app.rosters.roster_service import (
    get_active_roster,
    get_roster_stability_days,
    get_same_roster_matches_count,
    has_recent_roster_change,
)


class RosterServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.opponent = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.db.add_all([self.team, self.opponent])
        self.db.flush()
        self.players = [
            Player(nickname=f"p{index}", team_id=self.team.id, external_source="test", external_id=f"p{index}")
            for index in range(5)
        ]
        self.db.add_all(self.players)
        self.db.flush()
        self.at_date = datetime(2026, 1, 31, tzinfo=timezone.utc)
        for index, player in enumerate(self.players):
            self.db.add(
                TeamRoster(
                    team_id=self.team.id,
                    player_id=player.id,
                    role=str(index),
                    start_date=self.at_date - timedelta(days=40 if index < 4 else 10),
                    is_active=True,
                    source="test",
                )
            )
        self.db.add(
            Match(
                team_a_id=self.team.id,
                team_b_id=self.opponent.id,
                tournament_name="The International",
                status="finished",
                start_time=self.at_date - timedelta(days=5),
                winner_team_id=self.team.id,
                is_tier1_match=True,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_active_roster_selected_by_date(self):
        roster = get_active_roster(self.db, self.team.id, self.at_date)

        self.assertEqual(len(roster), 5)

    def test_roster_stability_days_calculated(self):
        self.assertEqual(get_roster_stability_days(self.db, self.team.id, self.at_date), 10)

    def test_recent_roster_change_detected(self):
        self.assertTrue(has_recent_roster_change(self.db, self.team.id, self.at_date, days=30))

    def test_same_roster_matches_count(self):
        self.assertEqual(get_same_roster_matches_count(self.db, self.team.id, self.at_date), 1)

    def test_unknown_roster_safe_defaults(self):
        self.assertEqual(get_active_roster(self.db, 999, self.at_date), [])
        self.assertEqual(get_roster_stability_days(self.db, 999, self.at_date), 0)

    def test_cross_source_identity_uses_dated_real_roster(self):
        pandascore_team = Team(
            name="Team Liquid",
            external_source="pandascore",
            external_id="liquid-panda",
            is_active_tier1=True,
            tier="tier1",
        )
        self.db.add(pandascore_team)
        self.db.flush()
        for index, player in enumerate(self.players):
            self.db.add(
                TeamRoster(
                    team_id=pandascore_team.id,
                    player_id=player.id,
                    role=str(index),
                    start_date=None,
                    is_active=True,
                    source="pandascore",
                )
            )
        self.db.commit()

        roster = get_active_roster(self.db, pandascore_team.id, self.at_date)

        self.assertEqual({entry.team_id for entry in roster}, {self.team.id})
        self.assertEqual(get_roster_stability_days(self.db, pandascore_team.id, self.at_date), 10)
        self.assertEqual(get_same_roster_matches_count(self.db, pandascore_team.id, self.at_date), 1)

    def test_real_team_never_uses_dev_seed_roster(self):
        pandascore_team = Team(
            name="Team Spirit",
            external_source="pandascore",
            external_id="spirit-panda",
            is_active_tier1=True,
            tier="tier1",
        )
        dev_team = Team(
            name="Team Spirit",
            external_source="dev_seed",
            external_id="spirit-dev",
            is_active_tier1=True,
            tier="tier1",
        )
        self.db.add_all([pandascore_team, dev_team])
        self.db.flush()
        for index, player in enumerate(self.players):
            self.db.add_all(
                [
                    TeamRoster(
                        team_id=pandascore_team.id,
                        player_id=player.id,
                        role=str(index),
                        start_date=None,
                        is_active=True,
                        source="pandascore",
                    ),
                    TeamRoster(
                        team_id=dev_team.id,
                        player_id=player.id,
                        role=str(index),
                        start_date=self.at_date - timedelta(days=20),
                        is_active=True,
                        source="dev_seed",
                    ),
                ]
            )
        self.db.commit()

        roster = get_active_roster(self.db, pandascore_team.id, self.at_date)

        self.assertEqual({entry.team_id for entry in roster}, {pandascore_team.id})
        self.assertEqual(get_roster_stability_days(self.db, pandascore_team.id, self.at_date), 0)


if __name__ == "__main__":
    unittest.main()
