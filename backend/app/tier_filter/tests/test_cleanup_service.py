from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Match, Team
from app.tier_filter.cleanup_service import (
    MATCH_TEAM_B_EXCLUDED_REASON,
    MATCH_TOURNAMENT_EXCLUDED_REASON,
    TEAM_EXCLUDED_REASON,
    cleanup_tier1_data,
)
from app.tier_filter.schemas import Tier1Config, Tier1TeamConfig, Tier1TournamentConfig
from app.tier_filter.tier1_matcher import Tier1Matcher


def build_matcher() -> Tier1Matcher:
    return Tier1Matcher(
        Tier1Config(
            teams=[
                Tier1TeamConfig(name="Team Liquid", aliases=["Liquid"], region="WEU"),
                Tier1TeamConfig(name="Team Spirit", aliases=["Spirit"], region="EEU"),
            ],
            tournaments=[
                Tier1TournamentConfig(name="The International", aliases=["TI"]),
            ],
        )
    )


class CleanupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.matcher = build_matcher()

        self.liquid = Team(name="Team Liquid", is_active_tier1=False)
        self.spirit = Team(name="Team Spirit", is_active_tier1=False)
        self.lower_team = Team(name="Random Stack", is_active_tier1=True, excluded_reason=None)
        self.db.add_all([self.liquid, self.spirit, self.lower_team])
        self.db.flush()

        self.tier1_match = Match(
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="TI",
            status="upcoming",
        )
        self.lower_team_match = Match(
            team_a_id=self.liquid.id,
            team_b_id=self.lower_team.id,
            tournament_name="TI",
            status="upcoming",
        )
        self.lower_tournament_match = Match(
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="Small Local Cup",
            status="upcoming",
        )
        self.db.add_all([self.tier1_match, self.lower_team_match, self.lower_tournament_match])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_dry_run_does_not_change_database(self):
        summary = cleanup_tier1_data(self.db, apply=False, matcher=self.matcher)
        self.db.refresh(self.lower_team)
        self.db.refresh(self.tier1_match)

        self.assertEqual(summary.mode, "dry-run")
        self.assertTrue(self.lower_team.is_active_tier1)
        self.assertIsNone(self.lower_team.excluded_reason)
        self.assertFalse(self.tier1_match.is_tier1_match)

    def test_apply_marks_lower_tier_team_as_excluded(self):
        cleanup_tier1_data(self.db, apply=True, matcher=self.matcher)
        self.db.refresh(self.lower_team)

        self.assertFalse(self.lower_team.is_active_tier1)
        self.assertIsNone(self.lower_team.tier)
        self.assertEqual(self.lower_team.excluded_reason, TEAM_EXCLUDED_REASON)

    def test_apply_marks_lower_tier_match_as_excluded(self):
        cleanup_tier1_data(self.db, apply=True, matcher=self.matcher)
        self.db.refresh(self.lower_team_match)

        self.assertFalse(self.lower_team_match.is_tier1_match)
        self.assertIn(MATCH_TEAM_B_EXCLUDED_REASON, self.lower_team_match.excluded_reason)

    def test_tier1_match_requires_both_teams_and_tournament(self):
        summary = cleanup_tier1_data(self.db, apply=True, matcher=self.matcher)
        self.db.refresh(self.tier1_match)
        self.db.refresh(self.lower_team_match)
        self.db.refresh(self.lower_tournament_match)

        self.assertEqual(summary.tier1_matches_count, 1)
        self.assertTrue(self.tier1_match.is_tier1_match)
        self.assertFalse(self.lower_team_match.is_tier1_match)
        self.assertFalse(self.lower_tournament_match.is_tier1_match)
        self.assertIn(MATCH_TOURNAMENT_EXCLUDED_REASON, self.lower_tournament_match.excluded_reason)


if __name__ == "__main__":
    unittest.main()
