from __future__ import annotations

from ml.explainability.factor_templates import render_factor_text


LIMITED_EXPLANATION = "Explanation is limited because not enough feature data is available."


def build_prediction_explanation(
    feature_values: dict,
    feature_importance: list,
    team_a_name: str,
    team_b_name: str,
    max_factors: int = 5,
) -> dict:
    if not feature_values or not feature_importance:
        return {
            "summary": LIMITED_EXPLANATION,
            "positive_factors": [],
            "negative_factors": [],
            "raw_feature_values": dict(feature_values or {}),
        }

    positive_factors = []
    negative_factors = []
    for item in feature_importance:
        feature_name = item.get("feature")
        if feature_name not in feature_values:
            continue
        impact = float(item.get("impact", 0.0))
        factor = {
            "factor": feature_name,
            "impact": impact,
            "text": render_factor_text(feature_name, impact, team_a_name, team_b_name),
        }
        if impact > 0:
            positive_factors.append(factor)
        elif impact < 0:
            negative_factors.append(factor)
        if len(positive_factors) + len(negative_factors) >= max_factors:
            break

    if not positive_factors and not negative_factors:
        summary = LIMITED_EXPLANATION
    else:
        summary = f"Top factors compare {team_a_name} against {team_b_name} using available pre-match features."

    return {
        "summary": summary,
        "positive_factors": positive_factors,
        "negative_factors": negative_factors,
        "raw_feature_values": dict(feature_values),
    }
