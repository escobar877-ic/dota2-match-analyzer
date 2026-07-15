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


class SmokeModel:
    def predict_proba(self, rows):
        return [[0.22, 0.78] for _row in rows]


class MLPredictionIntegrationSmokeTests(unittest.TestCase):
    def test_tier1_match_with_active_model_artifacts_returns_ensemble_prediction(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
            team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="The International",
                status="upcoming",
                start_time=datetime(2026, 1, 10, tzinfo=timezone.utc),
                is_tier1_match=True,
            )
            db.add(match)
            db.flush()
            db.add(
                MatchPrematchFeature(
                    match_id=match.id,
                    team_a_id=team_a.id,
                    team_b_id=team_b.id,
                    feature_version="prematch_v1",
                    features_json={"elo_diff": 25.0, "match_format": "bo3"},
                )
            )
            db.add(
                ModelVersion(
                    model_name="logistic_regression",
                    model_type="sklearn",
                    version="prematch_smoke",
                    trained_at=datetime(2026, 1, 9, tzinfo=timezone.utc),
                    artifact_path="temp",
                    artifact_metadata_json={"feature_version": "prematch_v1"},
                    is_active=True,
                )
            )
            db.commit()

            with tempfile.TemporaryDirectory() as tmpdir:
                model_path = Path(tmpdir) / "prematch_model.pkl"
                schema_path = Path(tmpdir) / "feature_schema.json"
                with model_path.open("wb") as file:
                    pickle.dump(SmokeModel(), file)
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
                    response = get_match_prediction(match.id, db=db)

            self.assertEqual(response.prediction_type, "ensemble")
            self.assertEqual(response.model_version, "ensemble_v1")
            self.assertFalse(response.fallback_used)
            self.assertTrue(response.components["formula"].available)
            self.assertTrue(response.components["ml"].available)
            self.assertEqual(response.components["ml"].model_version, "prematch_smoke")
            self.assertAlmostEqual(response.team_a_probability + response.team_b_probability, 1.0, places=4)
            self.assertIsNotNone(response.data_freshness["features_generated_at"])
            self.assertEqual(response.data_freshness["model_trained_at"], "2026-01-09T00:00:00")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
