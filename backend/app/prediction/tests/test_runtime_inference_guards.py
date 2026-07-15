from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Match, ModelVersion, Team
from app.prediction.ml_prediction_service import (
    MLPredictionUnavailable,
    PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES,
    try_predict_with_ml,
)
from app.prediction.schemas import FormulaPredictionResponse
from ml.features.build_prematch_features import is_tier1_feature_match


class RuntimeModel:
    feature_importances_ = [0.0] * len(PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES)

    def predict_proba(self, rows):
        return [[0.44, 0.56] for _row in rows]


class RuntimeInferenceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        team_a = Team(
            name="Team Liquid",
            external_source="pandascore",
            external_id="a",
            is_active_tier1=True,
            tier="tier1",
        )
        team_b = Team(
            name="Team Spirit",
            external_source="pandascore",
            external_id="b",
            is_active_tier1=True,
            tier="tier1",
        )
        self.db.add_all([team_a, team_b])
        self.db.flush()
        self.match = Match(
            external_source="pandascore",
            external_id="match-1",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
            status="upcoming",
            is_tier1_match=True,
        )
        self.preview_match = Match(
            external_source="pandascore",
            external_id="match-2",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="Esports World Cup",
            start_time=datetime(2026, 7, 21, tzinfo=timezone.utc),
            status="upcoming",
            is_tier1_match=False,
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
        )
        self.db.add_all([self.match, self.preview_match])
        self.db.add(
            ModelVersion(
                model_name="random_forest",
                model_type="sklearn",
                version="prematch_v3_runtime_test",
                trained_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
                artifact_path="temp",
                artifact_metadata_json={"feature_version": "prematch_v3"},
                is_active=True,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_exact_v3_differential_schema_can_use_v4_runtime_builder(self):
        schema = self._schema()
        features = {name: 0.0 for name in schema["feature_names"]}
        features["match_format"] = "BO3"

        with self._artifacts(schema), patch(
            "app.prediction.ml_prediction_service.build_features_for_match",
            return_value=features,
        ) as builder:
            result = try_predict_with_ml(self.db, self.match)

        self.assertIsInstance(result, FormulaPredictionResponse)
        self.assertEqual(
            result.data_freshness["runtime_feature_adapter"],
            "prematch_v3_from_prematch_v4_exact_differential_schema",
        )
        builder.assert_called_once_with(
            self.db,
            self.match,
            allow_verified_pro_inference=False,
        )

    def test_unknown_mismatch_remains_fail_closed(self):
        schema = {
            "feature_names": ["elo_diff"],
            "categorical_maps": {},
            "fill_values": {"elo_diff": 0.0},
        }
        with self._artifacts(schema), patch(
            "app.prediction.ml_prediction_service.build_features_for_match"
        ) as builder:
            result = try_predict_with_ml(self.db, self.match)

        self.assertIsInstance(result, MLPredictionUnavailable)
        self.assertIn("feature_version_mismatch", result.reason)
        builder.assert_not_called()

    def test_compatible_adapter_rejects_missing_runtime_field(self):
        schema = self._schema()
        features = {name: 0.0 for name in schema["feature_names"]}
        features.pop("elo_diff")
        with self._artifacts(schema), patch(
            "app.prediction.ml_prediction_service.build_features_for_match",
            return_value=features,
        ):
            result = try_predict_with_ml(self.db, self.match)

        self.assertIsInstance(result, MLPredictionUnavailable)
        self.assertEqual(result.reason, "runtime_feature_schema_incomplete: elo_diff")

    def test_verified_pro_inference_requires_explicit_flag(self):
        self.assertFalse(is_tier1_feature_match(self.preview_match))
        self.assertTrue(
            is_tier1_feature_match(
                self.preview_match,
                allow_verified_pro_inference=True,
            )
        )
        self.assertFalse(self.preview_match.is_training_eligible)

    def _schema(self) -> dict:
        names = sorted(PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES)
        return {
            "feature_names": names,
            "categorical_maps": {"match_format": {"BO3": 1}},
            "fill_values": {name: 0.0 for name in names},
        }

    def _artifacts(self, schema: dict):
        return patch.multiple(
            "app.prediction.ml_prediction_service.model_loader",
            model_artifacts_exist=lambda: True,
            load_active_model=lambda: RuntimeModel(),
            load_feature_schema=lambda: schema,
            load_calibrator=lambda: None,
        )


if __name__ == "__main__":
    unittest.main()
