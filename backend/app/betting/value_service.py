from __future__ import annotations

from typing import Any

from app.betting.schemas import MarketEvaluationRequest
from app.prediction.schemas import FormulaPredictionResponse


MIN_EDGE = 0.05
MIN_EXPECTED_VALUE = 0.03


def evaluate_market(
    prediction: FormulaPredictionResponse,
    request: MarketEvaluationRequest,
) -> dict[str, Any]:
    market_type, model_probabilities = _market_probabilities(prediction, request.market_type)
    supplied_odds = {
        "team_a": request.team_a_odds,
        "team_b": request.team_b_odds,
    }
    if "draw" in model_probabilities:
        if request.draw_odds is None:
            raise ValueError("draw_odds is required for a BO2 three-way market.")
        supplied_odds["draw"] = request.draw_odds

    implied = {outcome: 1.0 / supplied_odds[outcome] for outcome in model_probabilities}
    overround = sum(implied.values())
    no_vig = {outcome: value / overround for outcome, value in implied.items()}
    outcomes = []
    for outcome, probability in model_probabilities.items():
        odds = supplied_odds[outcome]
        outcomes.append(
            {
                "outcome": outcome,
                "model_probability": round(probability, 4),
                "decimal_odds": round(odds, 4),
                "implied_probability": round(implied[outcome], 4),
                "no_vig_probability": round(no_vig[outcome], 4),
                "edge": round(probability - no_vig[outcome], 4),
                "expected_value": round(probability * odds - 1.0, 4),
            }
        )

    best = max(outcomes, key=lambda item: (item["expected_value"], item["edge"]))
    guard_reasons = _paper_guard_reasons(prediction)
    if best["edge"] < MIN_EDGE:
        guard_reasons.append(f"Model edge is below {MIN_EDGE:.0%}.")
    if best["expected_value"] < MIN_EXPECTED_VALUE:
        guard_reasons.append(f"Expected value is below {MIN_EXPECTED_VALUE:.0%}.")
    paper_test_eligible = not guard_reasons
    return {
        "market_type": market_type,
        "overround": round(overround, 4),
        "outcomes": outcomes,
        "best_outcome": best["outcome"] if paper_test_eligible else None,
        "paper_test_eligible": paper_test_eligible,
        "recommendation": (
            "record_fixed_one_unit_paper_test"
            if paper_test_eligible
            else "no_paper_test"
        ),
        "guard_reasons": list(dict.fromkeys(guard_reasons)),
    }


def _market_probabilities(
    prediction: FormulaPredictionResponse,
    requested_market: str,
) -> tuple[str, dict[str, float]]:
    series = prediction.series_outcomes
    market = requested_market.strip().lower()
    if market == "auto":
        market = "series_result" if series else "map_winner"
    if market == "map_winner":
        return market, {
            "team_a": prediction.team_a_probability,
            "team_b": prediction.team_b_probability,
        }
    if market != "series_result" or not series:
        raise ValueError("Series outcomes are unavailable for this match format.")
    probabilities = {
        "team_a": float(series["team_a_win"]),
        "team_b": float(series["team_b_win"]),
    }
    if float(series.get("draw") or 0.0) > 0:
        probabilities["draw"] = float(series["draw"])
    return market, probabilities


def _paper_guard_reasons(prediction: FormulaPredictionResponse) -> list[str]:
    reasons = []
    if prediction.confidence == "low":
        reasons.append("Prediction confidence is low.")
    if not prediction.backtest_metrics_used:
        reasons.append("Backtest-driven weights are unavailable.")
    guarded_reasons = prediction.confidence_reasons or []
    blocked_fragments = (
        "roster data is incomplete",
        "roster change",
        "patch is very new",
        "components disagree",
        "calibration",
    )
    for reason in guarded_reasons:
        if any(fragment in reason.lower() for fragment in blocked_fragments):
            reasons.append(reason)
    return reasons
