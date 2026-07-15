from __future__ import annotations

import sys
import unittest
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
from app.db.models import DataSyncLog, Match
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log
from worker.data_ingestion.sync_matches import sync_matches
from worker.data_ingestion.sync_teams import sync_teams


class FakeClient:
    source_name = "opendota"
    enabled = True

    def get_matches(self):
        return ClientResponse(
            ok=True,
            data=[
                {
                    "match_id": 1,
                    "radiant_team_id": 10,
                    "dire_team_id": 20,
                    "radiant_name": "Random Stack",
                    "dire_name": "Other Stack",
                    "league_name": "Small Local Cup",
                    "start_time": 1767225600,
                }
            ],
        )

    def get_upcoming_matches(self):
        return ClientResponse(ok=False, error="no upcoming")


class FailingClient:
    source_name = "opendota"
    enabled = True

    def get_teams(self):
        return ClientResponse(ok=False, error="api unavailable")


class SyncLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_write_sync_log(self):
        write_sync_log(
            self.db,
            source="opendota",
            sync_type="matches",
            status="ok",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            counters=SyncCounters(records_seen=2, records_created=1),
        )

        log = self.db.query(DataSyncLog).one()
        self.assertEqual(log.records_seen, 2)
        self.assertEqual(log.records_created, 1)

    def test_dry_run_does_not_write_to_db(self):
        with patch("worker.data_ingestion.sync_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sync_matches.get_clients",
            return_value=[FakeClient()],
        ):
            result = sync_matches(dry_run=True)

        self.assertEqual(result["records_seen"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_sync_failure_writes_data_sync_logs(self):
        with patch("worker.data_ingestion.sync_teams.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sync_teams.get_clients",
            return_value=[FailingClient()],
        ):
            sync_teams()

        log = self.db.query(DataSyncLog).one()
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error_message, "api unavailable")


if __name__ == "__main__":
    unittest.main()
