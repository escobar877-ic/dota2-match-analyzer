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
from app.db.models import Match, MatchPrematchFeature, Team
from ml.features.feature_schema import FEATURE_VERSION
from ml.safety import MLSafetyError
from ml.training.dataset_builder import (
    DatasetRow,
    DATASET_METADATA,
    materialize_dataset,
    remove_forbidden_feature_columns,
    split_time_based,
    build_training_dataset,
)


class DatasetBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.tier1_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.tier1_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.tier1_a, self.tier1_b, self.lower])
        self.db.flush()

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(6):
            match = Match(
                team_a_id=self.tier1_a.id,
                team_b_id=self.tier1_b.id,
                tournament_name="TI",
                status="finished",
                start_time=start + timedelta(days=index),
                winner_team_id=self.tier1_a.id if index % 2 == 0 else self.tier1_b.id,
                is_tier1_match=True,
            )
            self.db.add(match)
            self.db.flush()
            self.db.add(
                MatchPrematchFeature(
                    match_id=match.id,
                    team_a_id=match.team_a_id,
                    team_b_id=match.team_b_id,
                    feature_version=FEATURE_VERSION,
                    features_json={
                        "elo_diff": index * 10,
                        "match_format": "bo3",
                        "winner_team_id": match.winner_team_id,
                        "kills": 99,
                    },
                )
            )

        lower_match = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.lower.id,
            tournament_name="TI",
            status="finished",
            start_time=start + timedelta(days=10),
            winner_team_id=self.lower.id,
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
        )
        self.db.add(lower_match)
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=lower_match.id,
                team_a_id=lower_match.team_a_id,
                team_b_id=lower_match.team_b_id,
                feature_version=FEATURE_VERSION,
                features_json={"elo_diff": -500},
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_dataset_builder_uses_only_tier1_matches(self):
        dataset = build_training_dataset(self.db, min_rows=1)
        self.assertEqual(len(dataset.rows), 6)
        self.assertTrue(all(row.match_id != 7 for row in dataset.rows))
        self.assertEqual(dataset.metadata, DATASET_METADATA)

    def test_dataset_builder_includes_verified_pro_without_active_tier1_teams(self):
        match = Match(
            external_source="pandascore",
            external_id="verified-pro-1",
            team_a_id=self.tier1_a.id,
            team_b_id=self.lower.id,
            tournament_name="The International",
            status="finished",
            start_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
            winner_team_id=self.tier1_a.id,
            is_tier1_match=True,
        )
        self.db.add(match)
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=match.id,
                team_a_id=match.team_a_id,
                team_b_id=match.team_b_id,
                feature_version=FEATURE_VERSION,
                features_json={"elo_diff": 50, "match_format": "bo3"},
            )
        )
        self.db.commit()

        dataset = build_training_dataset(self.db, min_rows=1)

        self.assertIn(match.id, {row.match_id for row in dataset.rows})

    def test_dataset_builder_excludes_dev_seed_by_default(self):
        match = Match(
            external_source="dev_seed",
            external_id="synthetic-1",
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="The International",
            status="finished",
            start_time=datetime(2026, 2, 2, tzinfo=timezone.utc),
            winner_team_id=self.tier1_a.id,
            is_tier1_match=True,
        )
        self.db.add(match)
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=match.id,
                team_a_id=match.team_a_id,
                team_b_id=match.team_b_id,
                feature_version=FEATURE_VERSION,
                features_json={"elo_diff": 60, "match_format": "bo3"},
            )
        )
        self.db.commit()

        dataset = build_training_dataset(self.db, min_rows=1)

        self.assertNotIn(match.id, {row.match_id for row in dataset.rows})

    def test_hybrid_profile_downweights_verified_pro_rows(self):
        match = Match(
            external_source="stratz",
            external_id="verified-pro-weighted",
            team_a_id=self.tier1_a.id,
            team_b_id=self.lower.id,
            tournament_name="Dota 2 Champions League",
            status="finished",
            start_time=datetime(2026, 2, 3, tzinfo=timezone.utc),
            winner_team_id=self.tier1_a.id,
            is_tier1_match=True,
            competition_tier="pro",
            verification_status="verified",
            is_training_eligible=True,
        )
        self.db.add(match)
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=match.id,
                team_a_id=match.team_a_id,
                team_b_id=match.team_b_id,
                feature_version=FEATURE_VERSION,
                features_json={"elo_diff": 25, "match_format": "bo3"},
            )
        )
        self.db.commit()

        dataset = build_training_dataset(
            self.db,
            min_rows=1,
            training_profile="tier1_plus_verified_pro",
        )
        row_index = next(index for index, row in enumerate(dataset.rows) if row.match_id == match.id)
        self.assertEqual(dataset.sample_weights[row_index], 0.5)
        self.assertTrue(dataset.metadata["tier1_evaluation_required"])

    def test_dataset_builder_rejects_non_tier1_metadata(self):
        rows = [DatasetRow(match_id=1, start_time=datetime.now(timezone.utc), features={"elo_diff": 1}, label=1)]
        with self.assertRaises(MLSafetyError):
            materialize_dataset(rows, {"tier1_only": False})

    def test_time_based_split_preserves_time_order(self):
        dataset = build_training_dataset(self.db, min_rows=1)
        split = split_time_based(dataset)
        self.assertLessEqual(split.train.rows[-1].start_time, split.validation.rows[0].start_time)
        self.assertLessEqual(split.validation.rows[-1].start_time, split.test.rows[0].start_time)

    def test_forbidden_post_match_columns_removed(self):
        cleaned = remove_forbidden_feature_columns({"elo_diff": 1, "winner_team_id": 10, "kills": 20})
        self.assertEqual(cleaned, {"elo_diff": 1})

    def test_missing_absolute_ratings_use_neutral_fill_values(self):
        rows = [
            DatasetRow(
                match_id=1,
                start_time=datetime.now(timezone.utc),
                features={"team_a_elo": None, "team_a_winrate_last_5": None, "elo_diff": None},
                label=1,
            )
        ]
        dataset = materialize_dataset(rows, DATASET_METADATA)
        values = dict(zip(dataset.feature_schema.feature_names, dataset.x[0]))
        self.assertEqual(values["team_a_elo"], 1500.0)
        self.assertEqual(values["team_a_winrate_last_5"], 0.5)
        self.assertEqual(values["elo_diff"], 0.0)

    def test_differential_feature_set_excludes_absolute_team_features(self):
        dataset = build_training_dataset(
            self.db,
            min_rows=1,
            training_profile="tier1_only",
            feature_set="differential",
        )
        self.assertIn("elo_diff", dataset.feature_schema.feature_names)
        self.assertNotIn("team_a_elo", dataset.feature_schema.feature_names)
        self.assertEqual(dataset.metadata["feature_set"], "differential")


if __name__ == "__main__":
    unittest.main()
