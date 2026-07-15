from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.patches.patch_service import calculate_days_since_patch, get_current_patch, get_patch_for_match, sync_patches_from_config


class PatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "patches.json"
        self.path.write_text(
            json.dumps(
                [
                    {"patch_name": "7.39", "patch_version": "7.39", "release_date": "2026-01-01", "is_current": False},
                    {"patch_name": "7.40", "patch_version": "7.40", "release_date": "2026-02-01", "is_current": True},
                ]
            ),
            encoding="utf-8",
        )
        sync_patches_from_config(self.db, self.path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()
        self.db.close()

    def test_patch_selected_by_match_date(self):
        patch = get_patch_for_match(self.db, datetime(2026, 1, 15, tzinfo=timezone.utc))

        self.assertEqual(patch.patch_version, "7.39")

    def test_current_patch_selected(self):
        self.assertEqual(get_current_patch(self.db).patch_version, "7.40")

    def test_days_since_patch_calculated(self):
        days = calculate_days_since_patch(self.db, datetime(2026, 2, 11, tzinfo=timezone.utc))

        self.assertEqual(days, 10)

    def test_unknown_patch_safe_default(self):
        empty_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(empty_engine)
        empty_db = Session(empty_engine)
        try:
            self.assertIsNone(get_patch_for_match(empty_db, datetime(2026, 1, 1, tzinfo=timezone.utc)))
        finally:
            empty_db.close()


if __name__ == "__main__":
    unittest.main()
