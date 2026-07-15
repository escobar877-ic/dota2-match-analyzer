from __future__ import annotations

from typing import Any


def get_linear_model_importance(model: Any, feature_names: list[str]) -> list[dict]:
    try:
        coefficients = getattr(model, "coef_", None)
        if coefficients is None:
            return []
        values = coefficients[0] if hasattr(coefficients[0], "__iter__") else coefficients
        return _format_importance(feature_names, values)
    except Exception:
        return []


def get_tree_model_importance(model: Any, feature_names: list[str]) -> list[dict]:
    try:
        values = getattr(model, "feature_importances_", None)
        if values is None:
            return []
        return _format_importance(feature_names, values)
    except Exception:
        return []


def get_feature_importance(model: Any, feature_names: list[str]) -> list[dict]:
    linear = get_linear_model_importance(model, feature_names)
    if linear:
        return linear
    return get_tree_model_importance(model, feature_names)


def _format_importance(feature_names: list[str], values) -> list[dict]:
    items = []
    for feature_name, value in zip(feature_names, values):
        impact = float(value)
        items.append(
            {
                "feature": feature_name,
                "impact": impact,
                "abs_impact": abs(impact),
            }
        )
    return sorted(items, key=lambda item: item["abs_impact"], reverse=True)
