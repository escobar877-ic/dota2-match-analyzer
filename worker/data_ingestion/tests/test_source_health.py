from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.data_ingestion.sources.base import SourceResult

from worker.data_ingestion.source_health import build_source_health_report


class SourceHealthTests(unittest.TestCase):
    def test_source_health_writes_report(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches"
        ) as fetch:
            fetch.return_value.ok = True
            fetch.return_value.error = None
            path = Path(temp_dir) / "source_health_report.json"
            report = build_source_health_report(artifact_path=path)
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("opendota", payload["sources"])
        self.assertIn("capabilities", report)

    def test_stratz_with_key_uses_health_check(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"STRATZ_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[]),
        ), patch(
            "worker.data_ingestion.sources.stratz_client.StratzSourceClient.health_check",
            return_value=SourceResult(ok=True, source="stratz", records=[{"data": {"__typename": "DotaQuery"}}]),
        ) as health:
            path = Path(temp_dir) / "source_health_report.json"
            report = build_source_health_report(artifact_path=path)

        health.assert_called_once()
        self.assertTrue(report["sources"]["stratz"]["can_connect"])
        self.assertIsNone(report["sources"]["stratz"]["last_error"])

    def test_pandascore_with_key_uses_health_check(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[]),
        ), patch(
            "worker.data_ingestion.sources.pandascore_client.PandaScoreSourceClient.health_check",
            return_value=SourceResult(ok=True, source="pandascore", records=[]),
        ) as health:
            path = Path(temp_dir) / "source_health_report.json"
            report = build_source_health_report(artifact_path=path)

        health.assert_called_once()
        self.assertTrue(report["sources"]["pandascore"]["can_connect"])
        self.assertIsNone(report["sources"]["pandascore"]["last_error"])
        self.assertNotIn("skipped unless API key", " ".join(report["warnings"]))


if __name__ == "__main__":
    unittest.main()
