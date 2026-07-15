from __future__ import annotations

import unittest

from app.prediction.series_outcomes import calculate_series_outcomes


class SeriesOutcomeTests(unittest.TestCase):
    def test_bo2_includes_draw_probability(self):
        outcomes = calculate_series_outcomes(0.6, "BO2")

        self.assertEqual(outcomes["team_a_win"], 0.36)
        self.assertEqual(outcomes["draw"], 0.48)
        self.assertEqual(outcomes["team_b_win"], 0.16)
        self.assertAlmostEqual(
            outcomes["team_a_win"] + outcomes["draw"] + outcomes["team_b_win"],
            1.0,
        )

    def test_bo3_converts_map_probability_to_series_probability(self):
        outcomes = calculate_series_outcomes(0.6, "Best of 3")

        self.assertEqual(outcomes["format"], "BO3")
        self.assertEqual(outcomes["team_a_win"], 0.648)
        self.assertEqual(outcomes["draw"], 0.0)

    def test_bo5_probabilities_sum_to_one(self):
        outcomes = calculate_series_outcomes(0.57, "bo5")

        self.assertAlmostEqual(
            outcomes["team_a_win"] + outcomes["draw"] + outcomes["team_b_win"],
            1.0,
        )

    def test_unknown_format_returns_no_series_claim(self):
        self.assertIsNone(calculate_series_outcomes(0.6, None))


if __name__ == "__main__":
    unittest.main()
