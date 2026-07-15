from __future__ import annotations

import sys
import unittest
import os
from datetime import datetime, timezone
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
from app.db.models import DataSyncLog, Team
from worker.data_ingestion.sources.base import SourceResult
from worker.data_ingestion.sync_historical_matches import sync_historical_matches


class SyncHistoricalMatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.db.add_all([Team(name="Team Liquid", is_active_tier1=True), Team(name="Team Spirit", is_active_tier1=True)])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_default_dry_run_does_not_write(self):
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[self._raw_match()]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                artifact_path=None,
            )

        self.assertEqual(report["mode"], "dry_run")
        self.assertFalse(report["apply_allowed"])
        self.assertEqual(report["source_trust_level"], "discovery")
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_apply_writes_sync_log(self):
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[self._raw_match()]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                dry_run=False,
                artifact_path=None,
            )

        self.assertEqual(report["mode"], "apply")
        self.assertIn("OpenDota generic feed is discovery-only", report["source_errors"][0])
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_apply_blocked_when_valid_rows_zero(self):
        raw = self._raw_match()
        raw["radiant_name"] = "Unknown Stack"
        raw["dire_name"] = "Another Unknown"
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[raw]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                dry_run=False,
                artifact_path=None,
            )

        self.assertEqual(report["mode"], "apply")
        self.assertIn("apply blocked", report["source_errors"][0])
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_excluded_samples_written_with_raw_fields(self):
        raw = self._raw_match()
        raw["radiant_name"] = "Unknown Stack"
        raw["dire_name"] = "Another Unknown"
        raw["league_name"] = "Unknown Cup"
        raw["leagueid"] = 777
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[raw]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                artifact_path=None,
            )

        self.assertEqual(report["would_exclude"], 1)
        self.assertEqual(report["recommendation"], "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source")
        self.assertEqual(len(report["excluded_samples"]), 1)
        sample = report["excluded_samples"][0]
        self.assertEqual(sample["raw_team_a"], "Unknown Stack")
        self.assertEqual(sample["raw_team_b"], "Another Unknown")
        self.assertEqual(sample["raw_tournament"], "Unknown Cup")
        self.assertEqual(sample["raw_tournament_id"], "777")
        self.assertEqual(sample["normalized_team_a"], "Unknown Stack")
        self.assertIn("team_a_not_tier1", sample["exclusion_reasons"])

    def test_allow_empty_apply_bypass_is_explicit(self):
        raw = self._raw_match()
        raw["radiant_name"] = "Unknown Stack"
        raw["dire_name"] = "Another Unknown"
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[raw]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                dry_run=False,
                allow_empty_apply=True,
                artifact_path=None,
            )

        self.assertEqual(report["mode"], "apply")
        self.assertEqual(report["records_created"], 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 1)

    def test_source_mapping_changes_unknown_to_mapped_valid(self):
        raw = self._raw_match()
        raw["radiant_name"] = "OD Liquid ID"
        raw["dire_name"] = "OD Spirit ID"
        raw["league_name"] = "OD TI League"
        mappings = {
            "opendota": {
                "teams": {"101": "Team Liquid", "102": "Team Spirit"},
                "tournaments": {"OD TI League": "The International"},
            }
        }
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sync_historical_matches.load_source_mappings",
            return_value=mappings,
        ), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[raw]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                artifact_path=None,
            )

        self.assertEqual(report["would_create"], 0)
        self.assertEqual(report["would_exclude"], 1)
        self.assertIn("opendota_generic_discovery_only", report["exclusion_reasons"])

    def test_opendota_trusted_mode_can_dry_run_valid_rows(self):
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[self._raw_match()]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                source_mode="trusted",
                artifact_path=None,
            )

        self.assertEqual(report["would_create"], 1)
        self.assertTrue(report["apply_allowed"])

    def test_opendota_upcoming_cannot_be_verified_in_discovery(self):
        raw = self._raw_match()
        raw["radiant_win"] = None
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="opendota", records=[raw]),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                artifact_path=None,
            )

        self.assertIn("opendota_unverified_upcoming", report["exclusion_reasons"])

    def test_pandascore_verified_pro_dry_run_accepts_current_named_tournament(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sync_historical_matches.get_session",
            return_value=self.db,
        ), patch(
            "worker.data_ingestion.sources.pandascore_client.PandaScoreSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="pandascore", records=[self._pandascore_match()]),
        ):
            report = sync_historical_matches(
                source="pandascore",
                start_date="2026-01-01",
                end_date="2026-06-27",
                artifact_path=None,
            )

        self.assertEqual(report["would_create"], 1)
        self.assertEqual(report["would_exclude"], 0)
        self.assertEqual(report["quality_scope"], "verified_pro")

    def test_pandascore_verified_pro_rejects_low_confidence_tournament(self):
        raw = self._pandascore_match(league_name="EPL World Series")
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sync_historical_matches.get_session",
            return_value=self.db,
        ), patch(
            "worker.data_ingestion.sources.pandascore_client.PandaScoreSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="pandascore", records=[raw]),
        ):
            report = sync_historical_matches(
                source="pandascore",
                start_date="2026-01-01",
                end_date="2026-06-27",
                artifact_path=None,
            )

        self.assertEqual(report["would_create"], 0)
        self.assertEqual(report["would_exclude"], 1)
        self.assertIn("tournament_not_verified_pro", report["exclusion_reasons"])

    def test_pandascore_verified_pro_dry_run_counts_existing_as_update(self):
        self.db.add(Team(external_source="pandascore", external_id="1651", name="Virtus.pro"))
        self.db.add(Team(external_source="pandascore", external_id="138842", name="HULIGANI"))
        self.db.flush()
        existing = __import__("app.db.models").db.models.Match(
            external_source="pandascore",
            external_id="1540327",
            team_a_id=self.db.query(Team).filter_by(external_id="1651").one().id,
            team_b_id=self.db.query(Team).filter_by(external_id="138842").one().id,
            tournament_name="The International",
            start_time=datetime(2026, 6, 27, tzinfo=timezone.utc),
            status="finished",
            winner_team_id=self.db.query(Team).filter_by(external_id="1651").one().id,
            is_tier1_match=True,
        )
        self.db.add(existing)
        self.db.commit()

        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sync_historical_matches.get_session",
            return_value=self.db,
        ), patch(
            "worker.data_ingestion.sources.pandascore_client.PandaScoreSourceClient.fetch_matches",
            return_value=SourceResult(ok=True, source="pandascore", records=[self._pandascore_match()]),
        ):
            report = sync_historical_matches(
                source="pandascore",
                start_date="2026-01-01",
                end_date="2026-06-27",
                artifact_path=None,
            )

        self.assertEqual(report["would_create"], 0)
        self.assertEqual(report["would_update"], 1)

    def test_source_errors_logged_no_crash(self):
        with patch("worker.data_ingestion.sync_historical_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sources.opendota_client.OpenDotaSourceClient.fetch_matches",
            return_value=SourceResult(ok=False, source="opendota", records=[], error="timeout"),
        ):
            report = sync_historical_matches(
                source="opendota",
                start_date="2024-01-01",
                end_date="2024-01-31",
                artifact_path=None,
            )

        self.assertEqual(report["status"], "warning")
        self.assertIn("timeout", report["source_errors"][0])

    def test_stratz_unsupported_date_range_does_not_crash(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.sync_historical_matches.get_session",
            return_value=self.db,
        ):
            report = sync_historical_matches(
                source="stratz",
                start_date="2026-01-01",
                end_date="2026-06-16",
                artifact_path=None,
            )

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["apply_allowed"])
        self.assertEqual(report["records_seen"], 0)
        self.assertIn("date-range historical fetch is not implemented", report["source_errors"][0])
        self.assertEqual(report["recommendation"], "use_stratz_match_ids_or_pandascore_schedule_or_csv_batch")

    def _raw_match(self):
        return {
            "match_id": 1,
            "radiant_team_id": 101,
            "dire_team_id": 102,
            "radiant_name": "Team Liquid",
            "dire_name": "Team Spirit",
            "league_name": "The International",
            "leagueid": 999,
            "start_time": int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
            "radiant_win": True,
        }

    def _pandascore_match(self, league_name: str = "The International"):
        return {
            "id": 1540327,
            "opponents": [
                {"opponent": {"id": 1651, "name": "Virtus.pro"}},
                {"opponent": {"id": 138842, "name": "HULIGANI"}},
            ],
            "league": {"id": 4106, "name": league_name},
            "serie": {"full_name": f"{league_name} 2026"},
            "begin_at": "2026-06-27T12:00:00+00:00",
            "status": "finished",
            "number_of_games": 3,
            "winner": {"id": 1651},
        }


if __name__ == "__main__":
    unittest.main()
