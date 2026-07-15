from __future__ import annotations

import json
import pickle
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.matches import get_match_prediction
from app.database import Base
from app.db.models import Match, MatchPrematchFeature, ModelVersion, Prediction, Team
from ml.features.feature_schema import FEATURE_VERSION


class DummyModel:
    def predict_proba(self, rows):
        return [[0.35, 0.65] for _row in rows]


class MLPredictionFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()

        self.tier1_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="TI",
            status="upcoming",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_tier1_match=True,
        )
        self.excluded_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.lower.id,
            tournament_name="TI",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
        )
        self.db.add_all([self.tier1_match, self.excluded_match])
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=self.tier1_match.id,
                team_a_id=self.tier1_match.team_a_id,
                team_b_id=self.tier1_match.team_b_id,
                feature_version="prematch_v1",
                features_json={"elo_diff": 10, "match_format": "bo3"},
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_no_active_model_version_uses_formula_fallback(self):
        response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.fallback_reason, "active_model_version_not_found")

    def test_missing_artifacts_uses_formula_fallback(self):
        self._add_active_model_version()
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ):
                response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.fallback_reason, "model_artifacts_not_found")

    def test_missing_features_uses_formula_fallback(self):
        self._add_active_model_version(feature_version=FEATURE_VERSION)
        self.db.query(MatchPrematchFeature).delete()
        self.db.commit()
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with model_path.open("wb") as file:
                pickle.dump(DummyModel(), file)
            schema_path.write_text(
                json.dumps({"feature_names": ["elo_diff"], "categorical_maps": {}, "fill_values": {"elo_diff": 0.0}}),
                encoding="utf-8",
            )
            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ), patch("app.prediction.ml_prediction_service.build_features_for_match", side_effect=ValueError("no features")):
                response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertTrue(response.fallback_used)
        self.assertIn("prematch_features_unavailable", response.fallback_reason)

    def test_non_tier1_match_returns_403_without_fallback(self):
        response = get_match_prediction(self.excluded_match.id, db=self.db)
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "This match is excluded from analysis because it is not Tier 1.")
        self.assertEqual(self.db.query(Prediction).count(), 0)

    def test_dummy_local_model_returns_ensemble_prediction(self):
        self._add_active_model_version()
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with model_path.open("wb") as file:
                pickle.dump(DummyModel(), file)
            schema_path.write_text(
                json.dumps(
                    {
                        "feature_names": ["elo_diff", "match_format"],
                        "categorical_maps": {"match_format": {"bo3": 1}},
                        "fill_values": {"elo_diff": 0.0, "match_format": 0.0},
                    }
                ),
                encoding="utf-8",
            )
            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ):
                response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "ensemble")
        self.assertFalse(response.fallback_used)
        self.assertAlmostEqual(response.team_a_probability + response.team_b_probability, 1.0, places=4)
        self.assertTrue(response.components["formula"].available)
        self.assertTrue(response.components["ml"].available)
        self.assertEqual(response.components["ml"].model_version, "prematch_test")

    def test_fallback_reason_returned_when_fallback_used(self):
        response = get_match_prediction(self.tier1_match.id, db=self.db)
        self.assertTrue(response.fallback_used)
        self.assertIsNotNone(response.fallback_reason)

    def test_active_model_does_not_read_newer_incompatible_feature_record(self):
        self.db.query(MatchPrematchFeature).delete()
        self.db.add(
            MatchPrematchFeature(
                match_id=self.tier1_match.id,
                team_a_id=self.tier1_match.team_a_id,
                team_b_id=self.tier1_match.team_b_id,
                feature_version="prematch_v2",
                features_json={"elo_diff": 999},
            )
        )
        self._add_active_model_version(feature_version="prematch_v1")
        self.db.commit()
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with model_path.open("wb") as file:
                pickle.dump(DummyModel(), file)
            schema_path.write_text(
                json.dumps(
                    {"feature_names": ["elo_diff"], "categorical_maps": {}, "fill_values": {"elo_diff": 0.0}}
                ),
                encoding="utf-8",
            )
            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ):
                response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertIn("feature_version_mismatch", response.fallback_reason)

    def _add_active_model_version(self, *, feature_version: str = "prematch_v1") -> None:
        self.db.add(
            ModelVersion(
                model_name="logistic_regression",
                model_type="sklearn",
                version="prematch_test",
                trained_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                artifact_path="ml/artifacts/prematch_model.pkl",
                artifact_metadata_json={"feature_version": feature_version},
                is_active=True,
            )
        )
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
