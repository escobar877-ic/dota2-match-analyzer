from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Hero, Match, MatchDraft, MatchPatchContext, DotaPatch, Team
from ml.features.draft_features import build_draft_features


class DraftFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", is_active_tier1=True)
        self.lower = Team(name="Lower Stack", is_active_tier1=False)
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()
        self.heroes = [
            Hero(hero_id=index + 1, name=f"hero_{index + 1}", localized_name=f"Hero {index + 1}", roles_json=["Carry", "Support"])
            for index in range(12)
        ]
        self.db.add_all(self.heroes)
        self.patch = DotaPatch(
            patch_name="7.40",
            patch_version="7.40",
            release_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_current=True,
        )
        self.db.add(self.patch)
        self.db.flush()
        self.current_time = datetime(2026, 2, 1, tzinfo=timezone.utc)
        self.current_match = self._match(self.team_a.id, self.team_b.id, 0, "upcoming", None, True)
        self.db.flush()
        self.db.add(MatchPatchContext(match_id=self.current_match.id, patch_id=self.patch.id, days_since_patch=31, is_current_patch=True))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_draft_features_safe_when_no_draft(self):
        features = build_draft_features(self.db, self.current_match)

        self.assertFalse(features["draft_available"])
        self.assertFalse(features["draft_complete"])
        self.assertEqual(features["team_a_pick_count"], 0)

    def test_draft_features_use_only_past_tier1_matches(self):
        past = self._match(self.team_a.id, self.team_b.id, 10, "finished", self.team_a.id, True)
        lower = self._match(self.team_a.id, self.lower.id, 8, "finished", self.lower.id, False)
        future = self._match(self.team_a.id, self.team_b.id, -2, "finished", self.team_a.id, True)
        self.db.flush()
        for item in [past, lower, future]:
            self.db.add(MatchPatchContext(match_id=item.id, patch_id=self.patch.id, days_since_patch=10, is_current_patch=True))
        self._add_pick_set(past, self.team_a.id, [0, 1, 2, 3, 4])
        self._add_pick_set(lower, self.team_a.id, [0, 1, 2, 3, 4])
        self._add_pick_set(future, self.team_a.id, [0, 1, 2, 3, 4])
        self._add_pick_set(self.current_match, self.team_a.id, [0, 1, 2, 3, 4])
        self._add_pick_set(self.current_match, self.team_b.id, [5, 6, 7, 8, 9])
        self.db.commit()

        features = build_draft_features(self.db, self.current_match)

        self.assertTrue(features["draft_available"])
        self.assertEqual(features["team_a_pick_count"], 5)
        self.assertIsNotNone(features["team_a_hero_pool_comfort"])
        self.assertEqual(features["team_a_patch_hero_winrate"], 1.0)

    def _match(self, team_a_id: int, team_b_id: int, days_ago: int, status: str, winner_id: int | None, is_tier1: bool) -> Match:
        match = Match(
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            tournament_name="The International" if is_tier1 else "Small Cup",
            start_time=self.current_time - timedelta(days=days_ago),
            status=status,
            winner_team_id=winner_id,
            is_tier1_match=is_tier1,
        )
        self.db.add(match)
        return match

    def _add_pick_set(self, match: Match, team_id: int, hero_indexes: list[int]) -> None:
        for index, hero_index in enumerate(hero_indexes):
            self.db.add(
                MatchDraft(
                    match_id=match.id,
                    team_id=team_id,
                    hero_id=self.heroes[hero_index].id,
                    action_type="pick",
                    pick_order=index + 1,
                    draft_order=index + 1,
                    source="test",
                )
            )


if __name__ == "__main__":
    unittest.main()
