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
from app.db.models import Match, MatchPrematchFeature, ModelVersion, Team
from app.prediction.ml_prediction_service import try_predict_with_ml


class ExplainableDummyModel:
    coef_ = [[0.06, -0.03]]

    def predict_proba(self, rows):
        return [[0.3, 0.7] for _row in rows]


class NoImportanceDummyModel:
    def predict_proba(self, rows):
        return [[0.45, 0.55] for _row in rows]


class MLPredictionExplainabilityTests(unittest.TestCase):
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
            tournament_name="The International",
            status="upcoming",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_tier1_match=True,
        )
        self.excluded_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.lower.id,
            tournament_name="The International",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
        )
        self.db.add_all([self.tier1_match, self.excluded_match])
        self.db.flush()
        self.db.add(
            MatchPrematchFeature(
                match_id=self.tier1_match.id,
                team_a_id=self.team_a.id,
                team_b_id=self.team_b.id,
                feature_version="prematch_v1",
                features_json={"elo_diff": 25.0, "h2h_team_a_winrate": 0.4, "unused_feature": 99},
            )
        )
        self.db.add(
            ModelVersion(
                model_name="logistic_regression",
                model_type="sklearn",
                version="prematch_explainability_test",
                trained_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                artifact_path="temp",
                artifact_metadata_json={"feature_version": "prematch_v1"},
                is_active=True,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_ml_prediction_response_contains_explanation(self):
        response = self._predict_with_model(ExplainableDummyModel())
        explanation = response.explanation

        self.assertEqual(response.prediction_type, "ml")
        self.assertIn("summary", explanation)
        self.assertIn("positive_factors", explanation)
        self.assertIn("negative_factors", explanation)
        self.assertIn("raw_feature_values", explanation)

    def test_explanation_only_references_existing_features(self):
        response = self._predict_with_model(ExplainableDummyModel())
        factors = response.explanation["positive_factors"] + response.explanation["negative_factors"]
        factor_names = {factor["factor"] for factor in factors}

        self.assertEqual(factor_names, {"elo_diff", "h2h_team_a_winrate"})
        self.assertNotIn("missing_feature", factor_names)

    def test_positive_and_negative_factors_are_returned(self):
        response = self._predict_with_model(ExplainableDummyModel())

        self.assertEqual(response.explanation["positive_factors"][0]["factor"], "elo_diff")
        self.assertEqual(response.explanation["negative_factors"][0]["factor"], "h2h_team_a_winrate")

    def test_missing_feature_importance_does_not_break_ml_prediction(self):
        response = self._predict_with_model(NoImportanceDummyModel())

        self.assertEqual(response.prediction_type, "ml")
        self.assertFalse(response.fallback_used)
        self.assertEqual(response.explanation["positive_factors"], [])
        self.assertEqual(response.explanation["negative_factors"], [])
        self.assertIn("limited", response.explanation["summary"].lower())

    def test_formula_fallback_still_works(self):
        self.db.query(ModelVersion).delete()
        self.db.commit()

        response = get_match_prediction(self.tier1_match.id, db=self.db)

        self.assertEqual(response.prediction_type, "formula")
        self.assertTrue(response.fallback_used)
        self.assertIsInstance(response.explanation, list)

    def test_non_tier1_still_returns_403(self):
        response = get_match_prediction(self.excluded_match.id, db=self.db)
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "This match is excluded from analysis because it is not Tier 1.")

    def _predict_with_model(self, model):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with model_path.open("wb") as file:
                pickle.dump(model, file)
            schema_path.write_text(
                json.dumps(
                    {
                        "feature_names": ["elo_diff", "h2h_team_a_winrate"],
                        "categorical_maps": {},
                        "fill_values": {"elo_diff": 0.0, "h2h_team_a_winrate": 0.0},
                    }
                ),
                encoding="utf-8",
            )

            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ):
                return try_predict_with_ml(self.db, self.tier1_match)


if __name__ == "__main__":
    unittest.main()
