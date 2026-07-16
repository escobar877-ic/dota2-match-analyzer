from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.data_sync import (
    get_data_coverage,
    get_data_sources_status,
    get_latest_sync_logs,
    get_match_detail_enrichment_report,
    get_match_validation,
    get_project_audit,
    get_real_batch_report,
    get_historical_fetch_plan,
    get_historical_sync_report,
    get_source_mappings_status,
    get_source_health,
    get_stratz_match_id_import,
    get_stratz_match_id_validation,
    get_sync_review,
    get_sync_logs,
    get_upcoming_sync_report,
)
from app.database import Base
from app.db.models import DataSyncLog


class DataSyncApiTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_data_sources_status_returns_valid_shape(self):
        response = get_data_sources_status(db=self.db)

        self.assertIn("sources", response)
        self.assertIn("opendota", response["sources"])
        self.assertIn("enabled", response["sources"]["opendota"])
        self.assertIn("last_sync_status", response["sources"]["opendota"])
        self.assertIn("capabilities", response)
        self.assertTrue(response["sources"]["csv_import"]["safe_to_sync"])

    def test_latest_logs_works_with_no_logs(self):
        response = get_latest_sync_logs(db=self.db)

        self.assertEqual(response["logs"]["opendota"], None)
        self.assertEqual(response["logs"]["stratz"], None)
        self.assertEqual(response["logs"]["pandascore"], None)

    def test_sync_logs_works(self):
        self.db.add(
            DataSyncLog(
                source="opendota",
                sync_type="matches",
                status="ok",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                records_seen=1,
                records_created=1,
                records_updated=0,
                records_excluded=0,
            )
        )
        self.db.commit()

        response = get_sync_logs(db=self.db)

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["source"], "opendota")

    def test_data_coverage_works_with_no_report(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-data-coverage-report.json"
        with patch("app.api.data_sync.COVERAGE_REPORT_PATH", missing_path):
            response = get_data_coverage(db=self.db)

        self.assertEqual(response["tier1_historical_matches_count"], 0)
        self.assertEqual(response["training_readiness"], "insufficient")
        self.assertIn("matches_by_tournament", response)

    def test_data_audit_returns_missing_when_report_missing(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-project-audit-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.AUDIT_REPORT_PATH", missing_path):
            response = get_project_audit()

        self.assertEqual(response["status"], "missing")
        self.assertIn("project_audit", response["message"])

    def test_data_audit_returns_existing_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "project_audit_report.json"
            path.write_text('{"status":"ok","warnings":[],"errors":[],"checks":{}}', encoding="utf-8")
            with patch("app.api.data_sync.AUDIT_REPORT_PATH", path):
                response = get_project_audit()

        self.assertEqual(response["status"], "ok")

    def test_match_validation_returns_missing_when_report_missing(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-match-validation-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.MATCH_VALIDATION_REPORT_PATH", missing_path):
            response = get_match_validation()

        self.assertEqual(response["status"], "missing")
        self.assertIn("match_validation", response["message"])

    def test_match_validation_returns_existing_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "match_validation_report.json"
            path.write_text('{"status":"warning","warnings":["x"],"errors":[],"suspect_matches":[]}', encoding="utf-8")
            with patch("app.api.data_sync.MATCH_VALIDATION_REPORT_PATH", path):
                response = get_match_validation()

        self.assertEqual(response["status"], "warning")
        self.assertEqual(response["warnings"], ["x"])

    def test_real_batch_report_returns_missing_when_report_missing(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-real-batch-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.REAL_BATCH_REPORT_PATH", missing_path):
            response = get_real_batch_report()

        self.assertEqual(response["status"], "missing")
        self.assertIn("real_batch_pipeline", response["message"])

    def test_real_batch_report_returns_existing_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "real_batch_pipeline_report.json"
            path.write_text('{"status":"warning","real_matches_after":0}', encoding="utf-8")
            with patch("app.api.data_sync.REAL_BATCH_REPORT_PATH", path):
                response = get_real_batch_report()

        self.assertEqual(response["status"], "warning")
        self.assertEqual(response["real_matches_after"], 0)

    def test_source_health_missing_and_existing_report(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-source-health-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.SOURCE_HEALTH_REPORT_PATH", missing_path):
            self.assertEqual(get_source_health()["status"], "missing")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_health_report.json"
            path.write_text('{"status":"ok","sources":{}}', encoding="utf-8")
            with patch("app.api.data_sync.SOURCE_HEALTH_REPORT_PATH", path):
                self.assertEqual(get_source_health()["status"], "ok")

    def test_historical_fetch_plan_and_sync_report_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = Path(temp_dir) / "historical_fetch_plan.json"
            sync = Path(temp_dir) / "historical_sync_report.json"
            plan.write_text('{"status":"warning","recommended_windows":[]}', encoding="utf-8")
            sync.write_text('{"status":"warning","records_seen":0}', encoding="utf-8")
            with patch("app.api.data_sync.HISTORICAL_FETCH_PLAN_PATH", plan):
                self.assertEqual(get_historical_fetch_plan()["status"], "warning")
            with patch("app.api.data_sync.HISTORICAL_SYNC_REPORT_PATH", sync):
                self.assertEqual(get_historical_sync_report()["records_seen"], 0)

    def test_sync_review_missing_and_existing_report(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-sync-review-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.SYNC_REVIEW_REPORT_PATH", missing_path):
            self.assertEqual(get_sync_review()["status"], "missing")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sync_review_report.json"
            path.write_text('{"status":"warning","valid_rows":0}', encoding="utf-8")
            with patch("app.api.data_sync.SYNC_REVIEW_REPORT_PATH", path):
                self.assertEqual(get_sync_review()["valid_rows"], 0)

    def test_source_mappings_status_works(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_mappings.json"
            path.write_text(
                '{"opendota":{"teams":{"101":"Team Liquid"},"tournaments":{"TI":"The International"}}}',
                encoding="utf-8",
            )
            with patch("app.api.data_sync.SOURCE_MAPPINGS_PATH", path):
                response = get_source_mappings_status()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["mapped_teams_count"], 1)
        self.assertEqual(response["mapped_tournaments_count"], 1)

    def test_source_mappings_status_supports_verified_mapping_objects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_mappings.json"
            path.write_text(
                json.dumps(
                    {
                        "opendota": {
                            "teams": {
                                "55": {
                                    "canonical_name": "Poor Rangers",
                                    "manual_verified": True,
                                    "verification_note": "Verified source ID.",
                                }
                            },
                            "tournaments": {"19785": "Esports World Cup"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("app.api.data_sync.SOURCE_MAPPINGS_PATH", path):
                response = get_source_mappings_status()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["mapped_teams_count"], 1)
        self.assertEqual(response["mapped_tournaments_count"], 1)

    def test_stratz_match_id_reports_missing_and_existing(self):
        missing_validation = Path(tempfile.gettempdir()) / "missing-stratz-match-id-validation.json"
        missing_import = Path(tempfile.gettempdir()) / "missing-stratz-match-id-import.json"
        for path in (missing_validation, missing_import):
            if path.exists():
                path.unlink()
        with patch("app.api.data_sync.STRATZ_MATCH_ID_VALIDATION_REPORT_PATH", missing_validation):
            self.assertEqual(get_stratz_match_id_validation()["status"], "missing")
        with patch("app.api.data_sync.STRATZ_MATCH_ID_IMPORT_REPORT_PATH", missing_import):
            self.assertEqual(get_stratz_match_id_import()["status"], "missing")

        with tempfile.TemporaryDirectory() as temp_dir:
            validation = Path(temp_dir) / "stratz_match_id_validation_report.json"
            import_report = Path(temp_dir) / "stratz_match_id_import_report.json"
            validation.write_text('{"status":"ok","safe_to_apply":true}', encoding="utf-8")
            import_report.write_text('{"status":"warning","records_seen":0}', encoding="utf-8")
            with patch("app.api.data_sync.STRATZ_MATCH_ID_VALIDATION_REPORT_PATH", validation):
                self.assertTrue(get_stratz_match_id_validation()["safe_to_apply"])
            with patch("app.api.data_sync.STRATZ_MATCH_ID_IMPORT_REPORT_PATH", import_report):
                self.assertEqual(get_stratz_match_id_import()["records_seen"], 0)

    def test_upcoming_sync_report_missing_and_existing(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-upcoming-sync-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.UPCOMING_SYNC_REPORT_PATH", missing_path):
            self.assertEqual(get_upcoming_sync_report()["status"], "missing")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "upcoming_sync_report.json"
            path.write_text('{"status":"ok","records_seen":2}', encoding="utf-8")
            with patch("app.api.data_sync.UPCOMING_SYNC_REPORT_PATH", path):
                self.assertEqual(get_upcoming_sync_report()["records_seen"], 2)

    def test_match_detail_enrichment_report_missing_and_existing(self):
        missing_path = Path(tempfile.gettempdir()) / "missing-match-detail-enrichment-report.json"
        if missing_path.exists():
            missing_path.unlink()
        with patch("app.api.data_sync.MATCH_DETAIL_ENRICHMENT_REPORT_PATH", missing_path):
            response = get_match_detail_enrichment_report()
            self.assertEqual(response["status"], "missing")
            self.assertIn("enrich_match_details", response["message"])

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "match_detail_enrichment_report.json"
            path.write_text(
                '{"status":"ok","matches_enriched":5,"stats_rows_created":10}',
                encoding="utf-8",
            )
            with patch("app.api.data_sync.MATCH_DETAIL_ENRICHMENT_REPORT_PATH", path):
                response = get_match_detail_enrichment_report()

        self.assertEqual(response["matches_enriched"], 5)
        self.assertEqual(response["stats_rows_created"], 10)


if __name__ == "__main__":
    unittest.main()
