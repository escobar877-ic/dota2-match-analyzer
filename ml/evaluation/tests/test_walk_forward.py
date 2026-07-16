from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ml.evaluation.walk_forward import (
    _create_model,
    build_walk_forward_folds,
    build_weight_optimization_report,
    evaluate_stability,
    optimize_ensemble_weights,
)
from ml.training.dataset_builder import DatasetRow


class WalkForwardTests(unittest.TestCase):
    def test_extra_trees_model_is_available_for_candidate_validation(self):
        self.assertEqual(_create_model("extra_trees").__class__.__name__, "ExtraTreesClassifier")

    def test_folds_never_train_on_evaluation_or_future_rows(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = [
            DatasetRow(
                match_id=index + 1,
                start_time=start + timedelta(days=index),
                features={"elo_diff": 0.0},
                label=index % 2,
                sample_weight=1.0 if index % 2 == 0 else 0.5,
            )
            for index in range(80)
        ]

        folds = build_walk_forward_folds(
            rows,
            folds_count=3,
            min_train_rows=20,
            min_eval_rows=5,
        )

        self.assertEqual(len(folds), 3)
        for fold in folds:
            train_dates = [rows[index].start_time for index in fold.train_indices]
            evaluation_dates = [rows[index].start_time for index in fold.evaluation_indices]
            self.assertLess(max(train_dates), min(evaluation_dates))
            self.assertTrue(all(rows[index].sample_weight == 1.0 for index in fold.evaluation_indices))

    def test_stability_gate_passes_consistent_metrics(self):
        folds = [
            {
                "evaluation_rows": 40,
                "metrics": {"ml": {"log_loss": 0.65}},
            }
            for _ in range(3)
        ]
        aggregate = {
            "ml": {
                "log_loss": 0.64,
                "brier_score": 0.225,
                "calibration_error": 0.07,
            },
            "elo": {"log_loss": 0.65, "brier_score": 0.23},
        }

        result = evaluate_stability(folds, aggregate)

        self.assertTrue(result["passed"])

    def test_stability_gate_rejects_bad_calibration_and_fold(self):
        folds = [
            {
                "evaluation_rows": 40,
                "metrics": {"ml": {"log_loss": value}},
            }
            for value in (0.65, 0.78, 0.69)
        ]
        aggregate = {
            "ml": {
                "log_loss": 0.70,
                "brier_score": 0.26,
                "calibration_error": 0.14,
            },
            "elo": {"log_loss": 0.66, "brier_score": 0.23},
        }

        result = evaluate_stability(folds, aggregate)

        self.assertFalse(result["passed"])
        self.assertTrue(any("calibration" in reason for reason in result["reasons"]))
        self.assertTrue(any("temporal fold" in reason for reason in result["reasons"]))

    def test_stability_gate_rejects_insufficient_evaluation(self):
        result = evaluate_stability(
            [{"evaluation_rows": 20, "metrics": {"ml": {"log_loss": 0.65}}}],
            {
                "ml": {
                    "log_loss": 0.65,
                    "brier_score": 0.23,
                    "calibration_error": 0.08,
                },
                "elo": {"log_loss": 0.66, "brier_score": 0.24},
            },
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["valid_folds"], 1)

    def test_weight_optimizer_respects_safety_limits(self):
        labels = [0, 1] * 30
        predictions = {
            "formula": [0.2 if label == 0 else 0.8 for label in labels],
            "elo": [0.45 if label == 0 else 0.55 for label in labels],
            "ml": [0.7 if label == 0 else 0.3 for label in labels],
        }

        weights = optimize_ensemble_weights(labels, predictions)

        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)
        self.assertTrue(all(0.10 <= value <= 0.65 for value in weights.values()))
        self.assertEqual(weights["formula"], 0.65)

    def test_weight_report_rejects_latest_fold_regression(self):
        outputs = []
        for fold in range(3):
            labels = [0, 1] * 20
            formula = [0.2 if label == 0 else 0.8 for label in labels]
            if fold == 2:
                formula = [1.0 - probability for probability in formula]
            outputs.append(
                {
                    "labels": labels,
                    "predictions": {
                        "formula": formula,
                        "elo": [0.45 if label == 0 else 0.55 for label in labels],
                        "ml": [0.48 if label == 0 else 0.52 for label in labels],
                    },
                }
            )

        report = build_weight_optimization_report(
            outputs,
            baseline_weights={"formula": 0.35, "elo": 0.25, "ml": 0.40},
            stability_passed=True,
        )

        self.assertFalse(report["production_approved"])
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["production_weights"], report["baseline_weights"])
        self.assertTrue(report["reasons"])


if __name__ == "__main__":
    unittest.main()
