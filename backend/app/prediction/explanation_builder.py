from app.prediction.schemas import PredictionFactors


def build_explanation(factors: PredictionFactors) -> list[str]:
    explanation: list[str] = []

    explanation.append(_factor_sentence("recent form", factors.recent_form))
    explanation.append(_factor_sentence("rating", factors.team_rating))
    explanation.append(_factor_sentence("head-to-head", factors.head_to_head))

    if abs(factors.hero_pool) >= 0.015:
        explanation.append(_factor_sentence("hero pool", factors.hero_pool))

    if abs(factors.roster_stability) >= 0.015:
        explanation.append(_factor_sentence("roster stability", factors.roster_stability))

    return explanation


def _factor_sentence(label: str, value: float) -> str:
    leader = "Team A" if value >= 0 else "Team B"
    advantage = abs(value)

    if advantage < 0.015:
        return f"{label.capitalize()} is close between the teams."
    if advantage < 0.05:
        return f"{leader} has a small {label} advantage."
    if advantage < 0.10:
        return f"{leader} has better {label}."
    return f"{leader} has a strong {label} advantage."
