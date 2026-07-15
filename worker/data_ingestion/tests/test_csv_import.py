from __future__ import annotations

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
from app.db.models import DataSyncLog, Match
from worker.data_ingestion.csv_import import import_csv


class CsvImportTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_csv_dry_run_does_not_write(self):
        path = self._write_csv(
            [
                "csv-1,Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=False)

        self.assertEqual(result["records_seen"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_cli_report_metadata_distinguishes_dry_run_from_apply(self):
        path = self._write_csv(
            [
                "csv-1,Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )
        artifact_path = Path(self.temp_dir.name) / "csv_import_report.json"

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=False, artifact_path=artifact_path)

        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["would_create"], 1)
        self.assertIn("generated_at", result)
        self.assertTrue(artifact_path.exists())
        self.assertFalse(artifact_path.with_name(f"{artifact_path.name}.tmp").exists())

    def test_csv_apply_imports_valid_tier1_match(self):
        path = self._write_csv(
            [
                "csv-1,Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=True)

        match = self.db.query(Match).one()
        log = self.db.query(DataSyncLog).one()
        self.assertTrue(match.is_tier1_match)
        self.assertEqual(result["created"], 1)
        self.assertEqual(log.source, "csv_import")
        self.assertEqual(log.records_created, 1)

    def test_unknown_team_is_excluded(self):
        path = self._write_csv(
            [
                "csv-1,Random Stack,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Spirit,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=True)

        self.assertEqual(result["excluded"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)

    def test_lower_tier_tournament_is_excluded(self):
        path = self._write_csv(
            [
                "csv-1,Team Liquid,Team Spirit,Small Local Cup,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=True)

        self.assertEqual(result["excluded"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)

    def test_duplicate_import_updates_instead_of_duplicating(self):
        path = self._write_csv(
            [
                "csv-1,Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            first = import_csv(path, apply=True)
            second = import_csv(path, apply=True)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(self.db.query(Match).count(), 1)

    def test_csv_import_handles_optional_fields_in_sync_metadata(self):
        path = self._write_template_csv(
            [
                "csv-1,series-1,1,Team Liquid,Team Spirit,Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,2,1,7.40,2400,https://example.com/vod,https://example.com/source"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            result = import_csv(path, apply=True)

        log = self.db.query(DataSyncLog).one()
        self.assertEqual(result["created"], 1)
        self.assertIn("csv-1", log.metadata_json["row_metadata"])
        self.assertEqual(log.metadata_json["row_metadata"]["csv-1"]["series_id"], "series-1")

    def test_duplicate_without_external_id_uses_normalized_tuple(self):
        path = self._write_csv(
            [
                ",Team Liquid,Team Spirit,The International,2026-01-10T18:00:00+00:00,BO3,finished,Team Liquid,7.40"
            ]
        )

        with patch("worker.data_ingestion.csv_import.get_session", return_value=self.db):
            first = import_csv(path, apply=True)
            second = import_csv(path, apply=True)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(self.db.query(Match).count(), 1)

    def _write_csv(self, rows: list[str]) -> Path:
        path = Path(self.temp_dir.name) / "matches.csv"
        path.write_text(
            "\n".join(
                [
                    "external_id,team_a_name,team_b_name,tournament_name,start_time,format,status,winner_team_name,patch_version",
                    *rows,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _write_template_csv(self, rows: list[str]) -> Path:
        path = Path(self.temp_dir.name) / "matches-template.csv"
        path.write_text(
            "\n".join(
                [
                    "external_id,series_id,game_number,team_a_name,team_b_name,radiant_team_name,dire_team_name,tournament_name,start_time,format,status,winner_team_name,team_a_score,team_b_score,patch_version,duration_seconds,vod_url,source_url",
                    *rows,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":
    unittest.main()
