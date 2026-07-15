from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.data_ingestion.real_batch_report import build_real_batch_report


class RealBatchReportTests(unittest.TestCase):
    def test_report_marks_dev_seed_only_and_real_dev_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "coverage.json").write_text(
                json.dumps(
                    {
                        "training_readiness": "insufficient",
                        "dev_seed_only": True,
                        "matches_by_source": {"dev_seed": 120},
                    }
                ),
                encoding="utf-8",
            )
            (root / "quality.json").write_text(
                json.dumps({"estimated_valid_rows": 1, "estimated_excluded_rows": 0, "warnings": [], "errors": []}),
                encoding="utf-8",
            )
            (root / "csv_import.json").write_text(
                json.dumps({"mode": "dry_run", "status": "ok", "would_create": 1, "warnings": [], "errors": []}),
                encoding="utf-8",
            )
            with patch.dict(
                "worker.data_ingestion.real_batch_report.PATHS",
                {
                    "coverage": root / "coverage.json",
                    "import_quality": root / "quality.json",
                    "csv_import": root / "csv_import.json",
                },
                clear=True,
            ):
                report = build_real_batch_report(artifact_path=None)

        self.assertTrue(report["dev_seed_only"])
        self.assertEqual(report["real_matches_after"], 0)
        self.assertEqual(report["imported_rows"], 0)
        self.assertEqual(report["would_import_rows"], 1)
        self.assertFalse(report["candidate_created"])
        self.assertEqual(report["backtest_status"], "not_run")
        self.assertTrue(any("dev_seed_only" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
