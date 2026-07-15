from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
from worker.data_ingestion.real_batch_validator import validate_real_batch


class RealBatchValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.team_a = Team(name="Team Liquid", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_accepts_valid_tier1_csv_row(self):
        report = validate_real_batch(self._csv(["m1,s1,1,Team Liquid,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Team Liquid,2,1,https://example.com/source"]), self.db, artifact_path=None)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["valid_rows"], 1)

    def test_rejects_invalid_winner_and_zero_valid_rows(self):
        report = validate_real_batch(self._csv(["m1,s1,1,Team Liquid,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Unknown,2,1,https://example.com/source"]), self.db, artifact_path=None)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("invalid_winner" in error for error in report["errors"]))
        self.assertTrue(any("0 valid rows" in error for error in report["errors"]))

    def test_warns_missing_source_url(self):
        report = validate_real_batch(self._csv(["m1,s1,1,Team Liquid,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Team Liquid,2,1,"]), self.db, artifact_path=None)

        self.assertEqual(report["status"], "warning")
        self.assertTrue(any("source_url missing" in warning for warning in report["warnings"]))

    def test_detects_duplicate_against_db(self):
        self.db.add(
            Match(
                external_source="csv_import",
                external_id="m1",
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                tournament_name="The International",
                start_time=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
                status="finished",
                winner_team_id=self.team_a.id,
                is_tier1_match=True,
            )
        )
        self.db.commit()

        report = validate_real_batch(self._csv(["m1,s1,1,Team Liquid,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Team Liquid,2,1,https://example.com/source"]), self.db, artifact_path=None)

        self.assertGreaterEqual(report["suspected_existing_duplicates"], 1)

    def _csv(self, rows: list[str]) -> Path:
        path = Path(self.temp_dir.name) / "batch.csv"
        path.write_text(
            "\n".join(
                [
                    "external_id,series_id,game_number,team_a_name,team_b_name,tournament_name,start_time,format,status,winner_team_name,team_a_score,team_b_score,source_url",
                    *rows,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":
    unittest.main()
