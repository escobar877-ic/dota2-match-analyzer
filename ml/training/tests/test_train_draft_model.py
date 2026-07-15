from __future__ import annotations

import json
import pickle
import shutil
import sys
import tempfile
from contextlib import contextmanager
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Hero, Match, MatchDraft, MatchPrematchFeature, ModelVersion, Team
from ml.features.draft_feature_schema import FEATURE_VERSION
from ml.training import train_draft_model as trainer
from ml.training.draft_dataset_builder import PREMATCH_FEATURE_VERSION


class TrainDraftModelTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = Path(tempfile.mkdtemp())
        self.candidates_dir = self.temp_dir / "draft_candidates"
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.heroes = [
            Hero(hero_id=index + 1, name=f"hero_{index + 1}", localized_name=f"Hero {index + 1}", roles_json=["Carry"])
            for index in range(12)
        ]
        self.db.add_all(self.heroes)
        self.db.flush()
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_train_creates_draft_candidate_and_artifacts(self):
        self._seed_matches(60)

        with self._patch_training_paths():
            report = trainer.train_draft_model(min_rows=50, model="logistic_regression", no_calibration=True)

        self.assertNotEqual(report.get("status"), "failed")
        self.assertEqual(report["feature_version"], FEATURE_VERSION)
        model_version = self.db.query(ModelVersion).one()
        self.assertFalse(model_version.is_active)
        self.assertEqual(model_version.status, "candidate")
        metadata = model_version.artifact_metadata_json
        self.assertTrue(metadata["draft_aware"])
        self.assertTrue(metadata["experimental"])
        self.assertEqual(metadata["feature_version"], FEATURE_VERSION)
        self.assertTrue(Path(metadata["model_path"]).exists())
        self.assertTrue(Path(metadata["feature_schema_path"]).exists())
        self.assertTrue(Path(metadata["training_report_path"]).exists())
        self.assertTrue(metadata["not_used_in_main_prediction"])

    def test_predict_proba_probabilities_sum_to_one(self):
        self._seed_matches(60)

        with self._patch_training_paths():
            report = trainer.train_draft_model(min_rows=50, model="logistic_regression", no_calibration=True)

        model_version = self.db.query(ModelVersion).one()
        with Path(model_version.artifact_metadata_json["model_path"]).open("rb") as file:
            model = pickle.load(file)
        schema = json.loads(Path(model_version.artifact_metadata_json["feature_schema_path"]).read_text(encoding="utf-8"))
        probability_sum = sum(model.predict_proba([[0.0] * len(schema["feature_names"])])[0])
        self.assertAlmostEqual(probability_sum, 1.0)

    def test_no_draft_data_returns_safe_message(self):
        self._seed_matches(20, with_draft=False)

        with self._patch_training_paths():
            report = trainer.train_draft_model(min_rows=1)

        self.assertEqual(report["status"], "failed")
        self.assertIn("No draft-aware training rows available", report["error"])
        self.assertEqual(self.db.query(ModelVersion).count(), 0)

    def test_insufficient_rows_returns_safe_message(self):
        self._seed_matches(3)

        with self._patch_training_paths():
            report = trainer.train_draft_model(min_rows=50)

        self.assertEqual(report["status"], "failed")
        self.assertIn("No draft-aware training rows available", report["error"])

    def test_one_class_target_returns_safe_message(self):
        self._seed_matches(60, one_class=True)

        with self._patch_training_paths():
            report = trainer.train_draft_model(min_rows=50)

        self.assertEqual(report["status"], "failed")
        self.assertIn("one class", report["error"])
        self.assertEqual(self.db.query(ModelVersion).count(), 0)

    def test_artifact_validation_rejects_broken_artifacts_and_cleans_tmp(self):
        self._seed_matches(60)

        with self._patch_training_paths(), patch(
            "ml.training.train_draft_model._validate_artifacts",
            side_effect=trainer.DraftTrainingError("broken artifacts"),
        ):
            report = trainer.train_draft_model(min_rows=50)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(list(self.candidates_dir.glob("*.tmp")), [])
        self.assertEqual(self.db.query(ModelVersion).count(), 0)

    def test_active_prematch_artifacts_not_modified(self):
        active_dir = self.temp_dir / "active"
        active_dir.mkdir()
        active_file = active_dir / "prematch_model.pkl"
        active_file.write_text("active", encoding="utf-8")
        before = active_file.read_text(encoding="utf-8")
        self._seed_matches(60)

        with self._patch_training_paths():
            trainer.train_draft_model(min_rows=50, no_calibration=True)

        self.assertEqual(active_file.read_text(encoding="utf-8"), before)

    @contextmanager
    def _patch_training_paths(self):
        with patch("ml.training.train_draft_model.DRAFT_CANDIDATES_DIR", self.candidates_dir), patch(
            "ml.training.train_draft_model.SessionLocal", return_value=self.db
        ):
            yield

    def _seed_matches(self, count: int, *, with_draft: bool = True, one_class: bool = False) -> None:
        for index in range(count):
            winner = self.team_a.id if one_class or index % 2 == 0 else self.team_b.id
            match = Match(
                external_source="dev_seed",
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                tournament_name="The International",
                start_time=self.start + timedelta(days=index),
                status="finished",
                winner_team_id=winner,
                is_tier1_match=True,
            )
            self.db.add(match)
            self.db.flush()
            self.db.add(
                MatchPrematchFeature(
                    match_id=match.id,
                    team_a_id=match.team_a_id,
                    team_b_id=match.team_b_id,
                    feature_version=PREMATCH_FEATURE_VERSION,
                    features_json={"elo_diff": index - 30, "match_format": "BO3"},
                )
            )
            if with_draft:
                self._add_pick_set(match, self.team_a.id, [0, 1, 2, 3, 4])
                self._add_pick_set(match, self.team_b.id, [5, 6, 7, 8, 9])
        self.db.commit()

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
