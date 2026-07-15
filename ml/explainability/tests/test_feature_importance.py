import unittest

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from ml.explainability.feature_importance import get_feature_importance


class FeatureImportanceTests(unittest.TestCase):
    def test_logistic_regression_coefficients_are_read(self):
        model = LogisticRegression(max_iter=200)
        model.fit([[0, 0], [1, 1], [2, 1], [3, 2]], [0, 0, 1, 1])
        importance = get_feature_importance(model, ["elo_diff", "form_diff_10"])
        self.assertEqual({item["feature"] for item in importance}, {"elo_diff", "form_diff_10"})

    def test_random_forest_importances_are_read(self):
        model = RandomForestClassifier(n_estimators=10, random_state=42)
        model.fit([[0, 0], [1, 1], [2, 1], [3, 2]], [0, 0, 1, 1])
        importance = get_feature_importance(model, ["elo_diff", "form_diff_10"])
        self.assertEqual({item["feature"] for item in importance}, {"elo_diff", "form_diff_10"})

    def test_unknown_model_returns_empty_list(self):
        self.assertEqual(get_feature_importance(object(), ["elo_diff"]), [])


if __name__ == "__main__":
    unittest.main()
