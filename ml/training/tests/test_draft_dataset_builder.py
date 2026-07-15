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
from app.db.models import Hero, Match, MatchDraft, MatchPrematchFeature, Team
from ml.features.draft_feature_schema import ALL_FEATURE_FIELDS, FEATURE_VERSION as DRAFT_FEATURE_VERSION
from ml.training.draft_dataset_builder import (
    NotEnoughDraftTrainingDataError,
    PREMATCH_FEATURE_VERSION,
    build_draft_dataset_rows,
    build_draft_training_dataset,
)


class DraftDatasetBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Lower Stack", is_active_tier1=False, excluded_reason="team_not_tier1")
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()
        self.heroes = [
            Hero(hero_id=index + 1, name=f"hero_{index + 1}", localized_name=f"Hero {index + 1}", roles_json=["Carry"])
            for index in range(12)
        ]
        self.db.add_all(self.heroes)
        self.db.flush()
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.tier1_with_draft = self._match(0, self.team_a.id, self.team_b.id, "finished", self.team_a.id, True)
        self.tier1_without_draft = self._match(1, self.team_a.id, self.team_b.id, "finished", self.team_b.id, True)
        self.lower_with_draft = self._match(2, self.team_a.id, self.lower.id, "finished", self.team_a.id, False)
        self.no_winner = self._match(3, self.team_a.id, self.team_b.id, "finished", None, True)
        self.upcoming = self._match(4, self.team_a.id, self.team_b.id, "upcoming", None, True)
        self.db.flush()
        self._add_prematch(self.tier1_with_draft)
        self._add_prematch(self.tier1_without_draft)
        self._add_prematch(self.lower_with_draft)
        self._add_prematch(self.no_winner)
        self._add_prematch(self.upcoming)
        self._add_pick_set(self.tier1_with_draft, self.team_a.id, [0, 1, 2, 3, 4])
        self._add_pick_set(self.tier1_with_draft, self.team_b.id, [5, 6, 7, 8, 9])
        self._add_pick_set(self.lower_with_draft, self.team_a.id, [0, 1, 2, 3, 4])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_uses_only_tier1_finished_matches_with_winner_and_draft(self):
        rows, summary = build_draft_dataset_rows(self.db)

        self.assertEqual(summary.feature_version, DRAFT_FEATURE_VERSION)
        self.assertEqual(summary.total_eligible_matches, 2)
        self.assertEqual(summary.draft_matches, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].match_id, self.tier1_with_draft.id)

    def test_includes_draft_v1_fields(self):
        dataset = build_draft_training_dataset(self.db, min_rows=1)

        self.assertEqual(dataset.metadata["feature_version"], DRAFT_FEATURE_VERSION)
        self.assertIn("draft_available", dataset.feature_schema.feature_names)
        self.assertIn("draft_synergy_diff", dataset.feature_schema.feature_names)
        self.assertTrue(all(field in dataset.rows[0].features for field in ALL_FEATURE_FIELDS))

    def test_safe_error_if_no_draft_data(self):
        self.db.query(MatchDraft).delete()
        self.db.commit()

        with self.assertRaises(NotEnoughDraftTrainingDataError) as raised:
            build_draft_training_dataset(self.db, min_rows=1)
        self.assertIn("No draft-aware training rows available", str(raised.exception))

    def test_draft_v1_remains_pinned_to_prematch_v3_schema(self):
        self.assertEqual(PREMATCH_FEATURE_VERSION, "prematch_v3")

    def _match(
        self,
        days: int,
        team_a_id: int,
        team_b_id: int,
        status: str,
        winner_id: int | None,
        is_tier1: bool,
    ) -> Match:
        match = Match(
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            tournament_name="The International" if is_tier1 else "Small Cup",
            start_time=self.start + timedelta(days=days),
            status=status,
            winner_team_id=winner_id,
            is_tier1_match=is_tier1,
            excluded_reason=None if is_tier1 else "lower_tier",
        )
        self.db.add(match)
        return match

    def _add_prematch(self, match: Match) -> None:
        self.db.add(
            MatchPrematchFeature(
                match_id=match.id,
                team_a_id=match.team_a_id,
                team_b_id=match.team_b_id,
                feature_version=PREMATCH_FEATURE_VERSION,
                features_json={"elo_diff": 10, "match_format": "BO3"},
            )
        )

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
