from __future__ import annotations

import csv
import sys
import tempfile
import unittest
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
from app.db.models import DataSyncLog, DraftSnapshot, Match, MatchDraft
from worker.data_ingestion.sources.base import SourceResult
from worker.data_ingestion.stratz_match_id_import import import_stratz_match_id_batch


class FakeStratzClient:
    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self.records = records or {}

    def fetch_match_details(self, match_id: str) -> SourceResult:
        record = self.records.get(match_id)
        return SourceResult(ok=True, source="stratz", records=[record] if record else [], error=None, warnings=[])


class StratzMatchIdImportTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_dry_run_does_not_write_db(self):
        path = self._write_rows([self._row()])
        with patch("worker.data_ingestion.stratz_match_id_import.get_session", return_value=self.db):
            result = import_stratz_match_id_batch(path, apply=False, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertEqual(result["would_create"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_apply_blocked_if_validation_not_safe(self):
        path = self._write_rows([self._row(source_url="")])
        with patch("worker.data_ingestion.stratz_match_id_import.get_session", return_value=self.db):
            result = import_stratz_match_id_batch(path, apply=True, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertFalse(result["safe_to_apply"])
        self.assertEqual(result["records_created"], 0)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 1)

    def test_apply_is_idempotent_with_same_match_id(self):
        path = self._write_rows([self._row()])
        with patch("worker.data_ingestion.stratz_match_id_import.get_session", return_value=self.db):
            first = import_stratz_match_id_batch(path, apply=True, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)
            second = import_stratz_match_id_batch(path, apply=True, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertEqual(first["records_created"], 1)
        self.assertEqual(second["records_updated"], 1)
        self.assertEqual(self.db.query(Match).count(), 1)

    def test_draft_data_imported_if_available(self):
        path = self._write_rows([self._row()])
        with patch("worker.data_ingestion.stratz_match_id_import.get_session", return_value=self.db):
            result = import_stratz_match_id_batch(path, apply=True, client=FakeStratzClient({"1": _raw_match(with_draft=True)}), artifact_path=None)

        self.assertEqual(result["draft_imported_count"], 1)
        self.assertGreaterEqual(self.db.query(MatchDraft).count(), 2)
        self.assertEqual(self.db.query(DraftSnapshot).count(), 1)

    def test_report_is_written(self):
        path = self._write_rows([self._row()])
        output = Path(self.temp_dir.name) / "import.json"
        with patch("worker.data_ingestion.stratz_match_id_import.get_session", return_value=self.db):
            result = import_stratz_match_id_batch(path, apply=False, client=FakeStratzClient({"1": _raw_match()}), artifact_path=output)

        self.assertTrue(output.exists())
        self.assertEqual(result["validation_status"], "ok")
        self.assertIn("generated_at", result)
        self.assertEqual(result["file"], str(path))

    def _write_rows(self, rows: list[dict[str, str]]) -> Path:
        path = Path(self.temp_dir.name) / "stratz_ids.csv"
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _row(self, **overrides: str) -> dict[str, str]:
        row = {
            "match_id": "1",
            "expected_team_a_name": "Team Liquid",
            "expected_team_b_name": "Team Spirit",
            "expected_tournament_name": "The International",
            "expected_start_date": "2026-01-01",
            "source_url": "https://example.com/source",
            "verification_note": "manual test",
        }
        row.update(overrides)
        return row


def _raw_match(*, with_draft: bool = False) -> dict:
    raw = {
        "id": 1,
        "startDateTime": "2026-01-01T12:00:00+00:00",
        "didRadiantWin": True,
        "radiantTeam": {"id": 10, "name": "Team Liquid"},
        "direTeam": {"id": 20, "name": "Team Spirit"},
        "league": {"id": 100, "name": "The International"},
    }
    if with_draft:
        raw["pickBans"] = [
            {"heroId": 1, "teamId": 10, "isPick": True, "order": 1},
            {"heroId": 2, "teamId": 20, "isPick": True, "order": 2},
            {"heroId": 3, "teamId": 10, "isPick": False, "order": 3},
        ]
    return raw


if __name__ == "__main__":
    unittest.main()
