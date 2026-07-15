from __future__ import annotations

import os
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.models.calibration import calibrate_probabilities_guarded
from ml.models.logistic_regression_model import create_logistic_regression_model
from ml.models.model_loader import load_active_model, load_feature_schema, model_artifacts_exist
from ml.models.random_forest_model import create_random_forest_model
from ml.safety import MLSafetyError, assert_allowed_model


class LocalModelTests(unittest.TestCase):
    def test_calibration_guard_requires_independent_temporal_holdout(self):
        calibrator, report = calibrate_probabilities_guarded(
            [0.5] * 12,
            [0, 1] * 6,
        )

        self.assertIsNone(calibrator)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["reason"], "not_enough_rows_for_temporal_calibration_guard")

    def test_calibration_guard_rejects_calibrator_that_hurts_late_rows(self):
        probabilities = [0.1, 0.9] * 10
        labels = [0, 1] * 10

        calibrator, report = calibrate_probabilities_guarded(probabilities, labels)

        self.assertIsNone(calibrator)
        self.assertFalse(report["accepted"])
        self.assertEqual(
            report["reason"],
            "calibration_did_not_improve_temporal_guard_metrics",
        )

    def test_allowed_models_are_created(self):
        self.assertEqual(create_logistic_regression_model().__class__.__name__, "Pipeline")
        self.assertEqual(create_random_forest_model().__class__.__name__, "RandomForestClassifier")

    def test_forbidden_model_rejected(self):
        with self.assertRaises(MLSafetyError):
            assert_allowed_model("xgboost_neural_net")

    def test_probabilities_are_between_zero_and_one(self):
        x = [[0.0], [1.0], [2.0], [3.0]]
        y = [0, 0, 1, 1]
        model = create_logistic_regression_model()
        model.fit(x, y)
        probabilities = model.predict_proba([[1.5], [4.0]])
        for row in probabilities:
            self.assertGreaterEqual(row[1], 0)
            self.assertLessEqual(row[1], 1)

    def test_artifacts_can_be_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "prematch_model.pkl"
            schema_path = Path(tmpdir) / "feature_schema.json"
            with model_path.open("wb") as file:
                pickle.dump({"model": "test"}, file)
            schema_path.write_text('{"feature_names": ["elo_diff"]}', encoding="utf-8")

            with patch("ml.models.model_loader.MODEL_ARTIFACT_PATH", model_path), patch(
                "ml.models.model_loader.FEATURE_SCHEMA_PATH", schema_path
            ):
                self.assertTrue(model_artifacts_exist())
                self.assertEqual(load_active_model(), {"model": "test"})
                self.assertEqual(load_feature_schema(), {"feature_names": ["elo_diff"]})


if __name__ == "__main__":
    unittest.main()
