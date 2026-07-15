from __future__ import annotations

import unittest

from ml.evaluation.model_tournament import choose_recommended_profile, model_profiles


class ModelTournamentTests(unittest.TestCase):
    def test_profiles_use_lightweight_local_sklearn_models(self):
        self.assertEqual(
            set(model_profiles()),
            {
                "random_forest_current",
                "random_forest_regularized",
                "extra_trees_regularized",
                "hist_gradient_boosting",
                "logistic_regression",
            },
        )

    def test_recommendation_ignores_unstable_lower_metric(self):
        results = {
            "unstable": {
                "stable": False,
                "aggregate_metrics": {"log_loss": 0.60, "brier_score": 0.20},
                "fold_log_loss_stddev": 0.2,
            },
            "stable": {
                "stable": True,
                "aggregate_metrics": {"log_loss": 0.66, "brier_score": 0.23},
                "fold_log_loss_stddev": 0.02,
            },
        }

        self.assertEqual(choose_recommended_profile(results), "stable")


if __name__ == "__main__":
    unittest.main()
