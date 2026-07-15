import unittest

from ml.explainability.explanation_builder import LIMITED_EXPLANATION, build_prediction_explanation


class ExplanationBuilderTests(unittest.TestCase):
    def test_empty_features_returns_limited_explanation(self):
        result = build_prediction_explanation({}, [], "Team A", "Team B")
        self.assertEqual(result["summary"], LIMITED_EXPLANATION)
        self.assertEqual(result["positive_factors"], [])
        self.assertEqual(result["negative_factors"], [])

    def test_mentions_only_existing_features(self):
        result = build_prediction_explanation(
            {"elo_diff": 50},
            [
                {"feature": "elo_diff", "impact": 0.06},
                {"feature": "missing_feature", "impact": 0.9},
            ],
            "Team A",
            "Team B",
        )
        factors = result["positive_factors"] + result["negative_factors"]
        self.assertEqual([factor["factor"] for factor in factors], ["elo_diff"])

    def test_positive_and_negative_factors_are_split(self):
        result = build_prediction_explanation(
            {"elo_diff": 50, "h2h_team_a_winrate": 0.35},
            [
                {"feature": "elo_diff", "impact": 0.06},
                {"feature": "h2h_team_a_winrate", "impact": -0.03},
            ],
            "Team A",
            "Team B",
        )
        self.assertEqual(result["positive_factors"][0]["factor"], "elo_diff")
        self.assertEqual(result["negative_factors"][0]["factor"], "h2h_team_a_winrate")


if __name__ == "__main__":
    unittest.main()
