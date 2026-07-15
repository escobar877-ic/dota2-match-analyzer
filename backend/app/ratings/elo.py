import math

STARTING_ELO = 1500.0


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))


def k_factor(tournament_tier: str | None) -> float:
    tier = (tournament_tier or "").strip().upper()
    if tier in {"S", "S-TIER", "TIER 1", "1"}:
        return 40.0
    if tier in {"A", "A-TIER", "TIER 2", "2"}:
        return 32.0
    if tier in {"B", "B-TIER", "TIER 3", "3"}:
        return 24.0
    return 20.0


def format_multiplier(match_format: str | None) -> float:
    value = (match_format or "").strip().lower()
    if value == "bo5":
        return 1.25
    if value == "bo3":
        return 1.15
    return 1.0


def recency_multiplier(match_index: int, total_matches: int) -> float:
    if total_matches <= 1:
        return 1.0
    progress = match_index / (total_matches - 1)
    return 0.75 + progress * 0.5


def update_elo(
    rating_a: float,
    rating_b: float,
    score_a: float,
    base_k: float,
    recency_weight: float,
    series_weight: float,
) -> tuple[float, float]:
    expected_a = expected_score(rating_a, rating_b)
    expected_b = 1 - expected_a
    score_b = 1 - score_a
    effective_k = base_k * recency_weight * series_weight
    delta_a = effective_k * (score_a - expected_a)
    delta_b = effective_k * (score_b - expected_b)
    return rating_a + delta_a, rating_b + delta_b


def uncertainty(matches_count: int) -> float:
    return max(60.0, 350.0 / math.sqrt(matches_count + 1))


def rating_feature_value(rating: float | None) -> float:
    if rating is None:
        return 0.5
    normalized = 0.5 + (rating - STARTING_ELO) / 800
    return max(0.2, min(0.8, normalized))
