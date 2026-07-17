from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.db.models import ModelVersion
from app.health import build_system_readiness


class SystemReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def test_ready_when_database_model_scheduler_and_coverage_are_healthy(self):
        artifact = self.root / "model.pkl"
        artifact.write_bytes(b"model")
        self.db.add(
            ModelVersion(
                model_name="random_forest",
                model_type="sklearn",
                version="prematch_test",
                artifact_path=str(artifact),
                is_active=True,
                status="active",
                trained_at=self.now - timedelta(days=1),
            )
        )
        self.db.commit()
        refresh = self.root / "refresh.json"
        refresh.write_text(
            json.dumps({"status": "ok", "generated_at": (self.now - timedelta(minutes=5)).isoformat()}),
            encoding="utf-8",
        )
        coverage = self.root / "coverage.json"
        coverage.write_text(
            json.dumps(
                {
                    "training_readiness": "good",
                    "real_tier1_historical_matches_count": 358,
                    "verified_pro_historical_matches_count": 812,
                    "patch_coverage_ratio": 1.0,
                    "roster_coverage_ratio": 0.78,
                }
            ),
            encoding="utf-8",
        )
        live_context = self.root / "live-context.json"
        live_context.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "generated_at": (self.now - timedelta(seconds=45)).isoformat(),
                    "matched_live_matches": 1,
                    "drafts_available": 1,
                }
            ),
            encoding="utf-8",
        )

        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=refresh,
            coverage_report_path=coverage,
            live_context_report_path=live_context,
        )

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["ready"])
        self.assertEqual(report["active_model_version"], "prematch_test")
        self.assertEqual(report["real_tier1_matches"], 358)
        self.assertEqual(report["checks"]["live_context_scheduler"]["status"], "ok")
        self.assertEqual(report["checks"]["live_context_scheduler"]["drafts_available"], 1)

    def test_stale_live_context_report_is_degraded_but_ready(self):
        live_context = self.root / "live-context.json"
        live_context.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "generated_at": (self.now - timedelta(minutes=10)).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=self.root / "missing-refresh.json",
            coverage_report_path=self.root / "missing-coverage.json",
            live_context_report_path=live_context,
        )

        self.assertTrue(report["ready"])
        self.assertEqual(report["checks"]["live_context_scheduler"]["status"], "warning")
        self.assertIn("stale", report["checks"]["live_context_scheduler"]["message"].lower())

    def test_missing_optional_runtime_state_is_degraded_but_fallback_ready(self):
        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=self.root / "missing-refresh.json",
            coverage_report_path=self.root / "missing-coverage.json",
        )

        self.assertEqual(report["status"], "warning")
        self.assertTrue(report["ready"])
        self.assertTrue(report["checks"]["active_model"]["fallback_available"])
        self.assertGreaterEqual(len(report["warnings"]), 3)

    def test_stale_scheduler_report_is_reported(self):
        refresh = self.root / "refresh.json"
        refresh.write_text(
            json.dumps({"status": "ok", "generated_at": (self.now - timedelta(hours=2)).isoformat()}),
            encoding="utf-8",
        )

        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=refresh,
            coverage_report_path=self.root / "missing-coverage.json",
        )

        self.assertEqual(report["checks"]["forecast_scheduler"]["status"], "warning")
        self.assertIn("stale", report["checks"]["forecast_scheduler"]["message"].lower())

    def test_historical_health_warning_does_not_hide_healthy_current_cycle(self):
        refresh = self.root / "refresh.json"
        refresh.write_text(
            json.dumps(
                {
                    "status": "warning",
                    "cycle_status": "ok",
                    "generated_at": (self.now - timedelta(minutes=5)).isoformat(),
                    "forecast_health": {"status": "warning"},
                }
            ),
            encoding="utf-8",
        )

        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=refresh,
            coverage_report_path=self.root / "missing-coverage.json",
        )

        self.assertEqual(report["checks"]["forecast_scheduler"]["status"], "ok")
        self.assertEqual(report["checks"]["forecast_scheduler"]["last_cycle_status"], "ok")

    def test_current_cycle_warning_degrades_scheduler_readiness(self):
        refresh = self.root / "refresh-warning.json"
        refresh.write_text(
            json.dumps(
                {
                    "status": "warning",
                    "cycle_status": "warning",
                    "generated_at": (self.now - timedelta(minutes=5)).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        report = build_system_readiness(
            self.db,
            now=self.now,
            refresh_report_path=refresh,
            coverage_report_path=self.root / "missing-coverage.json",
        )

        self.assertEqual(report["checks"]["forecast_scheduler"]["status"], "warning")
        self.assertIn("warnings", report["checks"]["forecast_scheduler"]["message"])


if __name__ == "__main__":
    unittest.main()
