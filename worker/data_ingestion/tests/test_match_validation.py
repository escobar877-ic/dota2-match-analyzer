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
from app.db.models import DataSyncLog, Match, Team
from worker.data_ingestion.match_validation import build_match_validation_report


class MatchValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False)
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()
        self.start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_detects_finished_match_without_winner(self):
        self._match("opendota", "m1", winner_id=None)
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("finished_missing_winner" in error for error in report["errors"]))

    def test_allows_finished_draw_without_winner(self):
        match = self._match("pandascore", "draw-1", winner_id=None)
        match.is_draw = True
        match.format = "BO2"
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertFalse(any("finished_missing_winner" in error for error in report["errors"]))
        self.assertFalse(any("winner_not_in_match_teams" in error for error in report["errors"]))

    def test_detects_winner_not_in_match_teams(self):
        self._match("opendota", "m1", winner_id=self.lower.id)
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("winner_not_in_match_teams" in error for error in report["errors"]))

    def test_detects_team_a_equals_team_b(self):
        self.db.add(
            Match(
                external_source="opendota",
                external_id="m1",
                team_a_id=self.team_a.id,
                team_b_id=self.team_a.id,
                tournament_name="The International",
                start_time=self.start_time,
                status="finished",
                winner_team_id=self.team_a.id,
                format="BO3",
                is_tier1_match=True,
            )
        )
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("team_a_equals_team_b" in error for error in report["errors"]))

    def test_detects_lower_tier_tournament_marked_tier1(self):
        self._match("opendota", "m1", tournament="Small Local Cup")
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("marked_tier1_but_invalid_tier1_context" in error for error in report["errors"]))

    def test_allows_verified_pro_match_with_non_tier1_team_allowlist(self):
        match = self._match("pandascore", "m1", tournament="The International")
        match.team_b_id = self.lower.id
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertFalse(any("team_b_not_tier1_allowlist" in error for error in report["errors"]))
        self.assertFalse(any("marked_tier1_but_invalid_tier1_context" in error for error in report["errors"]))

    def test_detects_duplicate_external_id(self):
        self._match("opendota", "dup", start_offset=0)
        self._match("opendota", "dup", start_offset=1)
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("duplicate external_source+external_id" in error for error in report["errors"]))

    def test_detects_possible_same_match_across_sources(self):
        self._match("opendota", "m1")
        self._match("stratz", "m2", start_offset=0.1)
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("possible_same_match_cross_source" in warning for warning in report["warnings"]))

    def test_writes_match_validation_report_json(self):
        self._match("csv_import", "m1")
        self.db.commit()
        path = Path(self.temp_dir.name) / "match_validation_report.json"

        report = build_match_validation_report(self.db, artifact_path=path)

        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("status", payload)
        self.assertIn("source_summary", payload)
        self.assertIn("suspect_matches", payload)
        self.assertEqual(payload["status"], report["status"])

    def test_catches_score_winner_mismatch_from_csv_metadata(self):
        match = self._match("csv_import", "csv-1", winner_id=self.team_a.id)
        self.db.flush()
        self.db.add(
            DataSyncLog(
                source="csv_import",
                sync_type="matches",
                status="ok",
                started_at=self.start_time,
                records_seen=1,
                records_created=1,
                records_updated=0,
                records_excluded=0,
                metadata_json={"row_metadata": {"csv-1": {"team_a_score": "0", "team_b_score": "2"}}},
            )
        )
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("score_winner_mismatch" in error for error in report["errors"]))

    def test_catches_series_id_game_number_duplicate_from_csv_metadata(self):
        self._match("csv_import", "csv-1")
        self._match("csv_import", "csv-2", start_offset=1)
        self.db.add(
            DataSyncLog(
                source="csv_import",
                sync_type="matches",
                status="ok",
                started_at=self.start_time,
                records_seen=2,
                records_created=2,
                records_updated=0,
                records_excluded=0,
                metadata_json={
                    "row_metadata": {
                        "csv-1": {"series_id": "s1", "game_number": "1"},
                        "csv-2": {"series_id": "s1", "game_number": "1"},
                    }
                },
            )
        )
        self.db.commit()

        report = build_match_validation_report(self.db, artifact_path=None)

        self.assertTrue(any("duplicate_series_id_game_number" in error for error in report["errors"]))

    def _match(
        self,
        source: str,
        external_id: str,
        *,
        winner_id: int | None | object = Ellipsis,
        tournament: str = "The International",
        start_offset: float = 0,
    ) -> Match:
        if winner_id is Ellipsis:
            winner_id = self.team_a.id
        match = Match(
            external_source=source,
            external_id=external_id,
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name=tournament,
            start_time=self.start_time + timedelta(hours=start_offset),
            status="finished",
            winner_team_id=winner_id,
            format="BO3",
            is_tier1_match=True,
        )
        self.db.add(match)
        return match


if __name__ == "__main__":
    unittest.main()
