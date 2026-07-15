import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from ml.evaluation.backtest import _dataset_type, _elo_probability, _model_feature_version
from ml.evaluation.model_quality_report import DEV_SEED_WARNING, build_model_quality_report


class BacktestTests(unittest.TestCase):
    def test_dataset_type_detection(self):
        self.assertEqual(_dataset_type({"dev_seed"}), "dev_seed")
        self.assertEqual(_dataset_type({"dev_seed", "opendota"}), "mixed")
        self.assertEqual(_dataset_type({"opendota"}), "real")
        self.assertEqual(_dataset_type(set()), "unknown")

    def test_elo_probability_is_bounded(self):
        self.assertGreater(_elo_probability({"elo_diff": 100}), 0.5)
        self.assertEqual(_elo_probability({}), 0.5)

    def test_active_feature_version_is_pinned_to_model_metadata(self):
        model = SimpleNamespace(
            artifact_metadata_json={"feature_version": "prematch_v4"},
            metrics_json={},
        )
        legacy = SimpleNamespace(artifact_metadata_json={}, metrics_json={})

        self.assertEqual(_model_feature_version(model), "prematch_v4")
        self.assertEqual(_model_feature_version(legacy), "prematch_v3")

    def test_quality_report_includes_dev_seed_warning(self):
        records = [
            {
                "match_id": 1,
                "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "label": 1,
                "formula": 0.7,
                "elo": 0.6,
                "ml": 0.8,
            },
            {
                "match_id": 2,
                "start_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "label": 0,
                "formula": 0.4,
                "elo": 0.5,
                "ml": 0.3,
            },
        ]
        report = build_model_quality_report(records, "dev_seed", ml_available=True)
        self.assertEqual(report["warning"], DEV_SEED_WARNING)
        self.assertEqual(report["matches_count"], 2)
        self.assertIn(report["best_by_log_loss"], {"formula", "elo", "ml"})


if __name__ == "__main__":
    unittest.main()
