import unittest
from pathlib import Path

from ml.config import ML_ARTIFACT_DIR, ML_FORBIDDEN_PACKAGES
from ml.safety import (
    MLSafetyError,
    assert_allowed_model,
    assert_no_forbidden_packages,
    assert_tier1_training_only,
    artifact_dir_exists,
)


class MLSafetyTests(unittest.TestCase):
    def test_allowed_model_passes(self):
        assert_allowed_model("logistic_regression")

    def test_forbidden_model_rejected(self):
        with self.assertRaises(MLSafetyError):
            assert_allowed_model("neural_network")

    def test_dataset_without_tier1_only_rejected(self):
        with self.assertRaises(MLSafetyError):
            assert_tier1_training_only({"source": "local_postgres"})

    def test_forbidden_packages_list_exists(self):
        self.assertIn("torch", ML_FORBIDDEN_PACKAGES)
        self.assertIn("tensorflow", ML_FORBIDDEN_PACKAGES)
        self.assertIn("transformers", ML_FORBIDDEN_PACKAGES)

    def test_artifact_dir_exists(self):
        self.assertTrue(artifact_dir_exists())
        self.assertTrue(Path(ML_ARTIFACT_DIR).is_dir())

    def test_no_forbidden_packages_importable(self):
        assert_no_forbidden_packages()


if __name__ == "__main__":
    unittest.main()
