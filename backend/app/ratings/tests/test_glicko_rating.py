from __future__ import annotations

import sys
import unittest
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ratings.glicko import GlickoState, update_glicko_like


class GlickoRatingTests(unittest.TestCase):
    def test_uncertainty_decreases_with_more_matches(self):
        state_a = GlickoState()
        state_b = GlickoState()

        for _ in range(8):
            state_a, state_b = update_glicko_like(state_a, state_b, 1.0)

        self.assertLess(state_a.rating_deviation, GlickoState().rating_deviation)
        self.assertGreaterEqual(state_a.rating_deviation, 60.0)

    def test_upset_changes_rating_more(self):
        favorite = GlickoState(rating=1700.0, rating_deviation=120.0, matches_count=20)
        underdog = GlickoState(rating=1300.0, rating_deviation=120.0, matches_count=20)

        expected_favorite, _ = update_glicko_like(favorite, underdog, 1.0)
        upset_favorite, _ = update_glicko_like(favorite, underdog, 0.0)

        expected_delta = abs(expected_favorite.rating - favorite.rating)
        upset_delta = abs(upset_favorite.rating - favorite.rating)
        self.assertGreater(upset_delta, expected_delta)


if __name__ == "__main__":
    unittest.main()
