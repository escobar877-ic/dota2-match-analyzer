from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
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
from app.db.models import Match, Team
from worker.data_ingestion.cross_source_match_resolver import (
    choose_preferred_source,
    find_possible_duplicate_matches,
    merge_match_metadata_safely,
)


class CrossSourceMatchResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.db.close()

    def test_detects_same_match_within_six_hours(self):
        left = self._match("opendota", "a", 0)
        right = self._match("stratz", "b", 2)
        self.db.commit()

        duplicates = find_possible_duplicate_matches(self.db, left, window_hours=6)

        self.assertEqual([match.id for match in duplicates], [right.id])

    def test_does_not_overwrite_stronger_source_with_weaker_source(self):
        stronger = self._match("stratz", "a", 0, winner_id=self.team_a.id)
        weaker = self._match("csv_import", "b", 0, winner_id=self.team_b.id)

        self.assertEqual(choose_preferred_source(stronger, weaker), "existing")
        decision = merge_match_metadata_safely(stronger, weaker)

        self.assertNotIn("winner_team_id", decision.updates)
        self.assertIn("winner_conflict", decision.warnings)

    def _match(self, source: str, external_id: str, hours: int, winner_id: int | None = None) -> Match:
        match = Match(
            external_source=source,
            external_id=external_id,
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            start_time=self.start + timedelta(hours=hours),
            status="finished",
            winner_team_id=winner_id,
            is_tier1_match=True,
        )
        self.db.add(match)
        self.db.flush()
        return match


if __name__ == "__main__":
    unittest.main()
