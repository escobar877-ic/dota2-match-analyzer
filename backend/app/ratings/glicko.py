from __future__ import annotations

import math
from dataclasses import dataclass


STARTING_GLICKO = 1500.0
STARTING_DEVIATION = 350.0
MIN_DEVIATION = 60.0
MAX_DEVIATION = 350.0


@dataclass
class GlickoState:
    rating: float = STARTING_GLICKO
    rating_deviation: float = STARTING_DEVIATION
    matches_count: int = 0


def expected_score(rating_a: float, rating_b: float, rd_b: float) -> float:
    scale = math.sqrt(1 + (3 * rd_b**2) / (math.pi**2 * 400**2))
    adjusted_diff = (rating_b - rating_a) / (400 * scale)
    return 1 / (1 + math.pow(10, adjusted_diff))


def update_glicko_like(
    state_a: GlickoState,
    state_b: GlickoState,
    score_a: float,
    *,
    match_weight: float = 1.0,
) -> tuple[GlickoState, GlickoState]:
    expected_a = expected_score(state_a.rating, state_b.rating, state_b.rating_deviation)
    expected_b = 1 - expected_a
    score_b = 1 - score_a
    upset_multiplier = 1.0 + min(1.0, abs(score_a - expected_a))
    k_a = _effective_k(state_a.rating_deviation, state_a.matches_count) * match_weight * upset_multiplier
    k_b = _effective_k(state_b.rating_deviation, state_b.matches_count) * match_weight * upset_multiplier

    next_a = GlickoState(
        rating=state_a.rating + k_a * (score_a - expected_a),
        rating_deviation=_next_deviation(state_a.rating_deviation, state_a.matches_count),
        matches_count=state_a.matches_count + 1,
    )
    next_b = GlickoState(
        rating=state_b.rating + k_b * (score_b - expected_b),
        rating_deviation=_next_deviation(state_b.rating_deviation, state_b.matches_count),
        matches_count=state_b.matches_count + 1,
    )
    return next_a, next_b


def _effective_k(rating_deviation: float, matches_count: int) -> float:
    uncertainty_factor = max(0.45, min(1.25, rating_deviation / 250))
    experience_factor = 1 / math.sqrt(matches_count + 1)
    return 42.0 * uncertainty_factor * max(0.55, experience_factor)


def _next_deviation(rating_deviation: float, matches_count: int) -> float:
    target = MAX_DEVIATION / math.sqrt(matches_count + 2)
    return max(MIN_DEVIATION, min(MAX_DEVIATION, min(rating_deviation, target)))
