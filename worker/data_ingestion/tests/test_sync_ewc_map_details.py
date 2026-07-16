from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
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
from app.db.models import DataSyncLog, Match, Team
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sync_ewc_map_details import sync_ewc_map_details


class FakeClient:
    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records if records is not None else [_raw_map()]

    def get_league_matches(self, league_id: str) -> ClientResponse:
        return ClientResponse(ok=True, data=self.records)


class FakeEnricher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "status": "ok",
            "records_seen": 1,
            "details_fetched": 1,
            "matches_enriched": 1,
            "records_excluded": 0,
            "skipped_existing": 0,
            "draft_entries_created": 24,
            "draft_entries_updated": 0,
            "source_errors": [],
        }


class SyncEwcMapDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_dry_run_is_read_only(self):
        report = self._run(apply=False)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["valid_tier1_maps"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_apply_creates_verified_map_and_calls_enrichment(self):
        enricher = FakeEnricher()

        report = self._run(apply=True, enricher=enricher)

        match = self.db.query(Match).one()
        self.assertEqual(report["records_created"], 1)
        self.assertTrue(match.is_tier1_match)
        self.assertTrue(match.is_training_eligible)
        self.assertEqual(match.verification_status, "verified")
        self.assertEqual(enricher.calls[0]["external_ids"], ["8899999001"])
        self.assertEqual(self.db.query(DataSyncLog).count(), 1)

    def test_existing_csv_map_is_updated_without_cross_source_duplicate(self):
        team_a = Team(name="Team Spirit", external_source="csv_import", external_id="7119388", is_active_tier1=True)
        team_b = Team(name="Team Yandex", external_source="csv_import", external_id="9823272", is_active_tier1=True)
        self.db.add_all([team_a, team_b])
        self.db.flush()
        self.db.add(
            Match(
                external_source="csv_import",
                external_id="8899999001",
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Esports World Cup",
                start_time=datetime(2026, 7, 16, tzinfo=UTC),
                status="finished",
                winner_team_id=team_a.id,
            )
        )
        self.db.commit()

        report = self._run(apply=True)

        self.assertEqual(report["records_created"], 0)
        self.assertEqual(report["records_updated"], 1)
        self.assertEqual(self.db.query(Match).count(), 1)
        self.assertTrue(self.db.query(Match).one().is_training_eligible)

        second = self._run(apply=True)
        self.assertEqual(second["records_created"], 0)
        self.assertEqual(second["records_updated"], 0)

    def test_unmapped_team_is_excluded(self):
        raw = _raw_map()
        raw["radiant_team_id"] = 999999999

        report = self._run(apply=False, client=FakeClient([raw]))

        self.assertEqual(report["valid_tier1_maps"], 0)
        self.assertEqual(report["records_excluded"], 1)
        self.assertIn("team_a_unmapped", report["exclusion_reasons"])

    def test_enrichment_error_is_reported_without_traceback(self):
        def broken_enricher(**kwargs):
            raise TimeoutError("detail endpoint unavailable")

        report = self._run(apply=True, enricher=broken_enricher)

        self.assertEqual(report["status"], "failed")
        self.assertIn("TimeoutError", report["errors"][0])
        self.assertEqual(self.db.query(Match).count(), 1)

    def _run(
        self,
        *,
        apply: bool,
        client: FakeClient | None = None,
        enricher=None,
    ) -> dict:
        output = Path(self.temp_dir.name) / "ewc.json"
        with patch("worker.data_ingestion.sync_ewc_map_details.get_session", return_value=self.db):
            return sync_ewc_map_details(
                apply=apply,
                client=client or FakeClient(),
                artifact_path=output,
                enricher=enricher or FakeEnricher(),
                sleep_seconds=0,
            )


def _raw_map() -> dict:
    return {
        "match_id": 8899999001,
        "leagueid": 19785,
        "start_time": int(datetime(2026, 7, 16, tzinfo=UTC).timestamp()),
        "duration": 2400,
        "radiant_team_id": 7119388,
        "dire_team_id": 9823272,
        "radiant_win": True,
    }


if __name__ == "__main__":
    unittest.main()
