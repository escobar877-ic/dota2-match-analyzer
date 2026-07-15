from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Hero, Match, MatchDraft, MatchPrematchFeature, Team
from ml.evaluation import draft_backtest
from ml.training import train_draft_model
from ml.training.draft_dataset_builder import PREMATCH_FEATURE_VERSION


class DraftBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = Path(tempfile.mkdtemp())
        self.candidates_dir = self.temp_dir / "draft_candidates"
        self.report_path = self.temp_dir / "draft_backtest_report.json"
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
        self._seed_matches(60)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_draft_backtest_writes_report_and_compares_models(self):
        with self._patch_paths():
            train_report = train_draft_model.train_draft_model(min_rows=50, model="logistic_regression", no_calibration=True)
            report = draft_backtest.run_draft_backtest(candidate_version=train_report["version"], min_rows=30)

        self.assertTrue(self.report_path.exists())
        self.assertTrue(report["draft_model_used"])
        self.assertIn("formula", report["compared_models"])
        self.assertIn("elo", report["compared_models"])
        self.assertIn("draft_model", report["compared_models"])
        self.assertIn("log_loss", report["metrics"]["draft_model"])

    def test_draft_backtest_has_dev_seed_warning(self):
        with self._patch_paths():
            train_draft_model.train_draft_model(min_rows=50, model="logistic_regression", no_calibration=True)
            report = draft_backtest.run_draft_backtest(min_rows=30)

        self.assertEqual(report["dataset_type"], "dev_seed")
        self.assertTrue(any("Synthetic dev seed" in warning for warning in report["warnings"]))

    @contextmanager
    def _patch_paths(self):
        with patch("ml.training.train_draft_model.DRAFT_CANDIDATES_DIR", self.candidates_dir), patch(
            "ml.training.train_draft_model.SessionLocal", return_value=self.db
        ), patch("ml.evaluation.draft_backtest.DRAFT_BACKTEST_REPORT_PATH", self.report_path), patch(
            "ml.evaluation.draft_backtest.SessionLocal", return_value=self.db
        ):
            yield

    def _seed_matches(self, count: int) -> None:
        for index in range(count):
            match = Match(
                external_source="dev_seed",
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                tournament_name="The International",
                start_time=self.start + timedelta(days=index),
                status="finished",
                winner_team_id=self.team_a.id if index % 2 == 0 else self.team_b.id,
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
