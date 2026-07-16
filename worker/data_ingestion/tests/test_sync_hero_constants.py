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
from app.db.models import Hero
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sync_hero_constants import sync_hero_constants


class FakeClient:
    def get_heroes(self) -> ClientResponse:
        return ClientResponse(
            ok=True,
            data={
                "53": {
                    "id": 53,
                    "name": "npc_dota_hero_furion",
                    "localized_name": "Nature's Prophet",
                    "primary_attr": "int",
                    "roles": ["Carry", "Pusher"],
                },
                "invalid": {"id": None},
            },
        )


class SyncHeroConstantsTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.db.add(Hero(hero_id=53, name="hero_53", localized_name="Hero 53", roles_json=[]))
        self.db.commit()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_dry_run_reports_placeholder_without_writing(self):
        with patch("worker.data_ingestion.sync_hero_constants.get_session", return_value=self.db):
            report = sync_hero_constants(apply=False, client=FakeClient(), artifact_path=None)

        hero = self.db.query(Hero).one()
        self.assertEqual(report["records_seen"], 1)
        self.assertEqual(report["placeholders_replaced"], 1)
        self.assertEqual(hero.localized_name, "Hero 53")

    def test_apply_updates_placeholder_and_writes_report(self):
        output = Path(self.temp_dir.name) / "heroes.json"
        with patch("worker.data_ingestion.sync_hero_constants.get_session", return_value=self.db):
            report = sync_hero_constants(apply=True, client=FakeClient(), artifact_path=output)

        hero = self.db.query(Hero).one()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(hero.localized_name, "Nature's Prophet")
        self.assertEqual(hero.roles_json, ["Carry", "Pusher"])
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
