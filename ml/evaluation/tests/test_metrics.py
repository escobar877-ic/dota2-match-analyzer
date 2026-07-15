import unittest

from ml.evaluation.metrics import calculate_classification_metrics


class MetricsTests(unittest.TestCase):
    def test_metrics_do_not_crash_on_small_data(self):
        metrics = calculate_classification_metrics([1], [0.7])
        self.assertIsNotNone(metrics["accuracy"])
        self.assertIsNotNone(metrics["log_loss"])
        self.assertIsNotNone(metrics["brier_score"])
        self.assertIsNone(metrics["roc_auc"])

    def test_metrics_with_two_classes_include_roc_auc(self):
        metrics = calculate_classification_metrics([0, 1], [0.2, 0.8])
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertIsNotNone(metrics["roc_auc"])


if __name__ == "__main__":
    unittest.main()
