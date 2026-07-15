from __future__ import annotations

from typing import Any

from app.prediction.schemas import FormulaPredictionResponse


SUPPORTED_FORMATS = {"BO1", "BO2", "BO3", "BO5"}


def calculate_series_outcomes(map_probability: float, match_format: str | None) -> dict[str, Any] | None:
    normalized_format = _normalize_format(match_format)
    if normalized_format not in SUPPORTED_FORMATS:
        return None

    p = min(0.9999, max(0.0001, float(map_probability)))
    q = 1.0 - p
    if normalized_format == "BO1":
        team_a_win = p
        draw = 0.0
        team_b_win = q
    elif normalized_format == "BO2":
        team_a_win = p**2
        draw = 2 * p * q
        team_b_win = q**2
    elif normalized_format == "BO3":
        team_a_win = p**2 * (3 - 2 * p)
        draw = 0.0
        team_b_win = 1.0 - team_a_win
    else:
        team_a_win = 10 * p**3 * q**2 + 5 * p**4 * q + p**5
        draw = 0.0
        team_b_win = 1.0 - team_a_win

    rounded_a = round(team_a_win, 4)
    rounded_draw = round(draw, 4)
    rounded_b = round(1.0 - rounded_a - rounded_draw, 4)
    return {
        "format": normalized_format,
        "probability_unit": "series_outcome",
        "team_a_win": rounded_a,
        "draw": rounded_draw,
        "team_b_win": rounded_b,
        "method": "independent_map_probability_v1",
        "assumption_warning": (
            "Series outcomes assume a stable per-map probability and independent maps; "
            "draft order and adaptation between maps are not modeled."
        ),
    }


def attach_series_outcomes(
    prediction: FormulaPredictionResponse,
    match_format: str | None,
) -> FormulaPredictionResponse:
    outcomes = calculate_series_outcomes(prediction.team_a_probability, match_format)
    return prediction.model_copy(
        update={
            "probability_unit": "map_strength",
            "series_outcomes": outcomes,
        }
    )


def _normalize_format(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    text = str(value).strip().upper().replace("BEST OF ", "BO").replace(" ", "")
    return text if text in SUPPORTED_FORMATS else "UNKNOWN"
