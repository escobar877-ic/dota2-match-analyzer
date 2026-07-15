from __future__ import annotations

import sys
import tempfile
import unittest
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
from app.db.models import Match
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sync_ewc_matches import sync_ewc_matches


class FakePandaScoreClient:
    enabled = True

    def __init__(self) -> None:
        pass

    def get_matches(self, **kwargs) -> ClientResponse:
        return ClientResponse(ok=True, data=[_spirit_mouz_draw(), _spirit_mouz_draw()])

    def get_running_matches(self, **kwargs) -> ClientResponse:
        return ClientResponse(ok=True, data=[])

    def get_upcoming_matches(self, **kwargs) -> ClientResponse:
        return ClientResponse(ok=True, data=[])


class SyncEwcMatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_dry_run_dedupes_and_does_not_write(self):
        report = self._sync(apply=False)

        self.assertEqual(report["records_seen"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)
        self.assertTrue(report["sample_matches"][0]["is_draw"])

    def test_apply_imports_finished_bo2_draw_as_verified_pro(self):
        report = self._sync(apply=True)

        self.assertEqual(report["records_updated"] + report["records_created"], 1)
        match = self.db.query(Match).one()
        self.assertEqual(match.external_id, "1565624")
        self.assertEqual(match.team_a.name, "Team Spirit")
        self.assertEqual(match.team_b.name, "MOUZ")
        self.assertEqual(match.status, "finished")
        self.assertEqual(match.format, "BO2")
        self.assertTrue(match.is_draw)
        self.assertIsNone(match.winner_team_id)
        self.assertEqual(match.dataset_profile, "ewc_2026")
        self.assertEqual(match.verification_status, "verified")
        self.assertFalse(match.is_training_eligible)

    def _sync(self, *, apply: bool):
        output = Path(self.temp_dir.name) / "ewc.json"
        with patch("worker.data_ingestion.sync_ewc_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sync_ewc_matches.PandaScoreClient",
            FakePandaScoreClient,
        ):
            return sync_ewc_matches(apply=apply, artifact_path=output)


def _spirit_mouz_draw() -> dict:
    return {
        "id": 1565624,
        "status": "finished",
        "begin_at": "2026-07-07T15:13:08Z",
        "number_of_games": 2,
        "winner": None,
        "winner_id": None,
        "results": [{"team_id": 1669, "score": 1}, {"team_id": 134559, "score": 1}],
        "opponents": [
            {"opponent": {"id": 1669, "name": "Team Spirit"}},
            {"opponent": {"id": 134559, "name": "MOUZ"}},
        ],
        "league": {"id": 5404, "name": "Esports World Cup", "slug": "dota-2-esports-world-cup"},
        "serie": {"full_name": "2026", "slug": "dota-2-esports-world-cup-2026"},
    }


if __name__ == "__main__":
    unittest.main()
