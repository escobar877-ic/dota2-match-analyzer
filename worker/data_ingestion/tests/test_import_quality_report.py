from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worker.data_ingestion.import_quality_report import build_import_quality_report


class ImportQualityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_quality_report_catches_unknown_team_invalid_winner_score_and_duplicates(self):
        path = self._write_csv(
            [
                "m1,s1,1,Random Stack,Team Spirit,Random Stack,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Random Stack,1,2,7.40,2000,https://example.com/vod,https://example.com/source",
                "m2,s1,1,Team Liquid,Team Spirit,Team Liquid,Team Spirit,The International,2026-01-01T10:00:00+00:00,BO3,finished,Unknown Winner,5,0,7.40,2000,not-a-url,https://example.com/source",
            ]
        )

        report = build_import_quality_report(path, artifact_path=None)

        self.assertEqual(report["status"], "failed")
        self.assertGreaterEqual(report["reason_counts"].get("team_a_not_tier1", 0), 1)
        self.assertGreaterEqual(report["reason_counts"].get("invalid_winner", 0), 1)
        self.assertGreaterEqual(report["reason_counts"].get("score_impossible_for_format", 0), 1)
        self.assertTrue(any("duplicate row" in warning for warning in report["warnings"]))

    def _write_csv(self, rows: list[str]) -> Path:
        path = Path(self.temp_dir.name) / "matches.csv"
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
