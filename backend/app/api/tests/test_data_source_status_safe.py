from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.data_sync import get_data_sources_status
from app.database import Base


class DataSourceStatusSafeTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_api_keys_are_not_exposed(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc123secretxyz"}, clear=False):
            response = get_data_sources_status(db=self.db)

        payload_text = str(response)
        self.assertNotIn("abc123secretxyz", payload_text)
        self.assertNotIn("secret", payload_text)
        self.assertTrue(response["sources"]["stratz"]["has_api_key"])

    def test_missing_keys_produce_setup_hint(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "", "PANDASCORE_API_KEY": ""}, clear=False):
            response = get_data_sources_status(db=self.db)

        self.assertFalse(response["sources"]["stratz"]["has_api_key"])
        self.assertIn("Set STRATZ_API_KEY", response["sources"]["stratz"]["setup_hint"])
        self.assertIn("Set PANDASCORE_API_KEY", response["sources"]["pandascore"]["setup_hint"])

    def test_status_endpoint_works_without_keys(self):
        with patch.dict(os.environ, {"OPENDOTA_API_KEY": "", "STRATZ_API_KEY": "", "PANDASCORE_API_KEY": ""}, clear=False):
            response = get_data_sources_status(db=self.db)

        self.assertEqual(set(response["sources"]), {"opendota", "stratz", "pandascore", "csv_import"})
        self.assertTrue(response["sources"]["opendota"]["enabled"])
        self.assertFalse(response["sources"]["stratz"]["enabled"])
        self.assertIn("capabilities", response)
        self.assertEqual(response["sources"]["stratz"]["missing_key_reason"], "STRATZ_API_KEY missing")


if __name__ == "__main__":
    unittest.main()
