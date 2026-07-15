from __future__ import annotations

import json
import sys
import tempfile
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
from worker.data_ingestion.project_audit import build_project_audit_report


class ProjectAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1", external_source="dev_seed", external_id="liquid")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1", external_source="dev_seed", external_id="spirit")
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_detects_duplicate_external_id(self):
        self._match("dup", days=2)
        self._match("dup", days=1)
        self.db.commit()

        report = build_project_audit_report(self.db, artifact_path=None)

        self.assertEqual(report["checks"]["duplicates"], "failed")
        self.assertTrue(any("duplicate external_source+external_id" in error for error in report["errors"]))

    def test_detects_invalid_finished_match_without_winner(self):
        self._match("m1", winner_id=None)
        self.db.commit()

        report = build_project_audit_report(self.db, artifact_path=None)

        self.assertEqual(report["checks"]["match_status"], "failed")
        self.assertTrue(any("finished match missing winner_team_id" in error for error in report["errors"]))

    def test_upcoming_match_is_valid_without_winner(self):
        self.db.add(
            Match(
                external_source="dev_seed",
                external_id="upcoming",
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                tournament_name="The International",
                start_time=datetime(2026, 1, 5, tzinfo=timezone.utc),
                status="upcoming",
                is_tier1_match=True,
            )
        )
        self.db.commit()

        report = build_project_audit_report(self.db, artifact_path=None)

        self.assertFalse(any("upcoming" in error for error in report["errors"]))

    def test_detects_dev_seed_only_warning(self):
        self._match("m1")
        self.db.commit()

        report = build_project_audit_report(self.db, artifact_path=None)

        self.assertTrue(any("dev_seed_only=true" in warning for warning in report["warnings"]))

    def test_writes_project_audit_report_json(self):
        self._match("m1")
        self.db.commit()
        path = Path(self.temp_dir.name) / "project_audit_report.json"

        report = build_project_audit_report(self.db, artifact_path=path)

        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("status", payload)
        self.assertIn("warnings", payload)
        self.assertIn("errors", payload)
        self.assertIn("checks", payload)
        self.assertEqual(payload["status"], report["status"])

    def _match(self, external_id: str, *, days: int = 1, winner_id: int | None | object = Ellipsis) -> Match:
        if winner_id is Ellipsis:
            winner_id = self.team_a.id
        match = Match(
            external_source="dev_seed",
            external_id=external_id,
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 1, 10, tzinfo=timezone.utc) - timedelta(days=days),
            status="finished",
            winner_team_id=winner_id,
            is_tier1_match=True,
            tournament_tier="tier1",
        )
        self.db.add(match)
        return match


if __name__ == "__main__":
    unittest.main()
