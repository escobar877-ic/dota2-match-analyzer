from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.patch_freshness import build_patch_freshness_report


class FakePatchClient:
    def __init__(self, response: ClientResponse) -> None:
        self.response = response

    def get_patches(self) -> ClientResponse:
        return self.response


class PatchFreshnessTests(unittest.TestCase):
    def test_matching_base_family_is_current_and_report_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._write_config(temp_dir, "7.41d")
            report_path = Path(temp_dir) / "patch_freshness_report.json"
            client = FakePatchClient(
                ClientResponse(ok=True, data=[{"name": "7.40", "id": 59}, {"name": "7.41", "id": 60}])
            )

            report = build_patch_freshness_report(
                client=client,
                config_path=config,
                check_database=False,
                artifact_path=report_path,
            )

            self.assertEqual(report["status"], "ok")
            self.assertTrue(report["family_matches"])
            self.assertFalse(report["stale"])
            self.assertTrue(report["manual_subpatch_review_required"])
            self.assertTrue(report_path.exists())
            self.assertFalse(report_path.with_name(f"{report_path.name}.tmp").exists())

    def test_newer_source_family_marks_config_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._write_config(temp_dir, "7.40b")
            report = build_patch_freshness_report(
                client=FakePatchClient(ClientResponse(ok=True, data=[{"name": "7.41", "id": 60}])),
                config_path=config,
                check_database=False,
                artifact_path=None,
            )

        self.assertEqual(report["status"], "warning")
        self.assertTrue(report["stale"])
        self.assertIn("update config/dota_patches.json", report["recommendation"])

    def test_source_failure_is_controlled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._write_config(temp_dir, "7.41d")
            report = build_patch_freshness_report(
                client=FakePatchClient(ClientResponse(ok=False, error="request timed out")),
                config_path=config,
                check_database=False,
                artifact_path=None,
            )

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["source_checked"])
        self.assertIn("request timed out", report["warnings"])

    def test_invalid_current_patch_config_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "patches.json"
            config.write_text(json.dumps([{"patch_version": "7.41", "is_current": False}]), encoding="utf-8")
            report = build_patch_freshness_report(
                client=FakePatchClient(ClientResponse(ok=True, data=[{"name": "7.41", "id": 60}])),
                config_path=config,
                check_database=False,
                artifact_path=None,
            )

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["errors"])

    @staticmethod
    def _write_config(directory: str, version: str) -> Path:
        path = Path(directory) / "patches.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "patch_name": version,
                        "patch_version": version,
                        "release_date": "2026-06-04",
                        "is_current": True,
                    }
                ]
            ),
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":
    unittest.main()
