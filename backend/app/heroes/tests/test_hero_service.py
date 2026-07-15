from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Hero
from app.heroes.hero_service import sync_heroes_from_config


class HeroServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_heroes_sync_from_config(self):
        path = Path(self.temp_dir.name) / "heroes.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "hero_id": 1,
                        "name": "npc_dota_hero_antimage",
                        "localized_name": "Anti-Mage",
                        "primary_attr": "agi",
                        "roles": ["Carry"],
                        "is_active": True,
                    }
                ]
            ),
            encoding="utf-8",
        )

        result = sync_heroes_from_config(self.db, path)

        hero = self.db.query(Hero).one()
        self.assertEqual(result["created"], 1)
        self.assertEqual(hero.localized_name, "Anti-Mage")
        self.assertEqual(hero.roles_json, ["Carry"])


if __name__ == "__main__":
    unittest.main()
