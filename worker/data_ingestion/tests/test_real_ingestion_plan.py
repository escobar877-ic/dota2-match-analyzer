from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
from worker.data_ingestion.real_ingestion_plan import build_real_ingestion_plan


class RealIngestionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.db.add(
            Match(
                external_source="dev_seed",
                external_id="m1",
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                tournament_name="The International",
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                status="finished",
                winner_team_id=self.team_a.id,
                is_tier1_match=True,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_plan_works_with_missing_keys_and_detects_dev_seed_only(self):
        with patch.dict("os.environ", {"STRATZ_API_KEY": "", "PANDASCORE_API_KEY": ""}, clear=False):
            report = build_real_ingestion_plan(self.db, artifact_path=None)

        self.assertEqual(report["status"], "warning")
        self.assertIn("csv_import", report["available_sources"])
        self.assertTrue(report["coverage"]["dev_seed_only"])
        self.assertGreater(report["coverage"]["usable_threshold_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
