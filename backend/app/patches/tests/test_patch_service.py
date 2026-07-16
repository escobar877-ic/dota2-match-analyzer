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
from app.db.models import Match, MatchPatchContext, Team
from app.patches.patch_service import (
    backfill_match_patch_contexts,
    calculate_days_since_patch,
    get_current_patch,
    get_patch_for_match,
    sync_patches_from_config,
)


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

    def test_backfill_creates_and_then_updates_context_idempotently(self):
        team_a = Team(name="Team Liquid", external_source="test", external_id="1")
        team_b = Team(name="Team Spirit", external_source="test", external_id="2")
        self.db.add_all([team_a, team_b])
        self.db.flush()
        match = Match(
            external_source="test",
            external_id="100",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 1, 15, tzinfo=timezone.utc),
            status="finished",
        )
        self.db.add(match)
        self.db.commit()

        first = backfill_match_patch_contexts(self.db)
        second = backfill_match_patch_contexts(self.db)

        self.assertEqual(first, {"created": 1, "updated": 0, "skipped": 0})
        self.assertEqual(second, {"created": 0, "updated": 1, "skipped": 0})
        self.assertEqual(self.db.query(MatchPatchContext).count(), 1)
        self.assertEqual(self.db.query(MatchPatchContext).one().patch.patch_version, "7.39")


if __name__ == "__main__":
    unittest.main()
