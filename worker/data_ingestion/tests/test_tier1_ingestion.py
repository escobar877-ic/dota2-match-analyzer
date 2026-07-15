from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.database import Base
from app.tier_filter.schemas import Tier1Config, Tier1TeamConfig, Tier1TournamentConfig
from app.tier_filter.tier1_matcher import Tier1Matcher
from worker.data_ingestion.db import upsert_team
from worker.data_ingestion.normalizer import NormalizedTeam


class Tier1IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.matcher = Tier1Matcher(
            Tier1Config(
                teams=[Tier1TeamConfig(name="Team Liquid")],
                tournaments=[Tier1TournamentConfig(name="The International")],
            )
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_data_ingestion_does_not_mark_unknown_team_as_tier1(self):
        team, was_created = upsert_team(
            self.db,
            NormalizedTeam(external_source="test", external_id="unknown-1", name="Unknown Stack"),
            matcher=self.matcher,
        )

        self.assertTrue(was_created)
        self.assertFalse(team.is_active_tier1)
        self.assertIsNone(team.tier)
        self.assertEqual(team.excluded_reason, "team_not_in_tier1_allowlist")


if __name__ == "__main__":
    unittest.main()
