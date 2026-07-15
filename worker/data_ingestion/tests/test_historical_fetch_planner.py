from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worker.data_ingestion.historical_fetch_planner import build_historical_fetch_plan
from worker.data_ingestion.source_status import SourceStatus


class HistoricalFetchPlannerTests(unittest.TestCase):
    def test_fetch_planner_writes_recommended_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "historical_fetch_plan.json"
            report = build_historical_fetch_plan(
                artifact_path=path,
                coverage={"tier1_historical_matches_count": 0, "training_readiness": "insufficient", "dev_seed_only": False},
                statuses={
                    "opendota": SourceStatus(enabled=True, has_api_key=False, last_sync_status="never", last_error=None),
                    "stratz": SourceStatus(enabled=False, has_api_key=False, last_sync_status="never", last_error="STRATZ_API_KEY missing"),
                    "pandascore": SourceStatus(enabled=False, has_api_key=False, last_sync_status="never", last_error="PANDASCORE_API_KEY missing"),
                },
            )
            self.assertTrue(path.exists())
        self.assertGreaterEqual(len(report["recommended_windows"]), 3)
        self.assertIn("available_sources", report)


if __name__ == "__main__":
    unittest.main()
