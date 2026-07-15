from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchPrematchFeature, ModelVersion
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors
from ml.explainability.explanation_builder import build_prediction_explanation
from ml.explainability.feature_importance import get_feature_importance
from ml.features.build_prematch_features import build_features_for_match
from ml.features.feature_schema import FEATURE_VERSION
from ml.models import model_loader
from ml.safety import assert_local_ml_only, assert_no_forbidden_packages


ML_PREDICTION_WARNING = "Local ML prediction. Formula/Elo remains available as fallback."

# The active prematch_v3 differential model uses fields whose definitions are
# unchanged in prematch_v4. Runtime adaptation is allowed only for this exact
# schema; all other version mismatches continue to fail closed.
PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES = frozenset(
    {
        "bo3_winrate_diff",
        "bo5_winrate_diff",
        "current_patch_winrate_diff",
        "days_since_patch",
        "elo_diff",
        "form_diff_10",
        "form_diff_20",
        "form_diff_5",
        "glicko_diff",
        "h2h_matches_count",
        "h2h_recent_weighted_score",
        "h2h_team_a_winrate",
        "is_elimination_match",
        "is_playoff",
        "match_format",
        "momentum_diff",
        "opponent_elo_diff_last_10",
        "patch_recency_weight",
        "rating_uncertainty_diff",
        "recency_weighted_form_diff",
        "roster_stability_diff",
        "same_roster_matches_diff",
        "strong_team_wins_diff",
        "tournament_recent_winrate_diff",
        "weak_loss_diff",
    }
)


@dataclass(frozen=True)
class MLPredictionUnavailable:
    reason: str


def try_predict_with_ml(
    db: Session,
    match: Match,
    *,
    allow_verified_pro_inference: bool = False,
) -> FormulaPredictionResponse | MLPredictionUnavailable:
    try:
        assert_local_ml_only()
        assert_no_forbidden_packages()
    except Exception as exc:
        return MLPredictionUnavailable(f"ml_safety_check_failed: {exc}")

    active_version = _get_active_model_version(db)
    if active_version is None:
        return MLPredictionUnavailable("active_model_version_not_found")
    if not model_loader.model_artifacts_exist():
        return MLPredictionUnavailable("model_artifacts_not_found")

    try:
        model = model_loader.load_active_model()
        feature_schema = model_loader.load_feature_schema()
    except Exception as exc:
        return MLPredictionUnavailable(f"model_artifacts_unreadable: {exc}")
    if model is None:
        return MLPredictionUnavailable("model_artifact_not_found")
    if feature_schema is None:
        return MLPredictionUnavailable("feature_schema_not_found")

    active_feature_version = _active_feature_version(active_version)
    feature_record = _get_prematch_feature_record(db, match.id, active_feature_version)
    runtime_adapter = None
    if feature_record is None:
        runtime_adapter = _runtime_feature_adapter(
            active_feature_version,
            FEATURE_VERSION,
            feature_schema,
        )
        if active_feature_version != FEATURE_VERSION and runtime_adapter is None:
            return MLPredictionUnavailable(
                "feature_version_mismatch: "
                f"active={active_feature_version},current={FEATURE_VERSION}"
            )
        try:
            features = build_features_for_match(
                db,
                match,
                allow_verified_pro_inference=allow_verified_pro_inference,
            )
            features_generated_at = None
        except Exception as exc:
            return MLPredictionUnavailable(f"prematch_features_unavailable: {exc}")
        missing_runtime_fields = _missing_schema_fields(features, feature_schema)
        if missing_runtime_fields:
            return MLPredictionUnavailable(
                "runtime_feature_schema_incomplete: " + ",".join(missing_runtime_fields)
            )
    else:
        features = feature_record.features_json
        features_generated_at = feature_record.generated_at.isoformat() if feature_record.generated_at else None

    try:
        encoded = _encode_features(features, feature_schema)
        team_a_probability = _predict_team_a_probability(model, encoded)
        try:
            calibrator = model_loader.load_calibrator()
        except Exception:
            calibrator = None
        if calibrator is not None:
            team_a_probability = _calibrate_probability(calibrator, team_a_probability)
        team_a_probability, team_b_probability = _normalize_probabilities(team_a_probability)
    except Exception as exc:
        return MLPredictionUnavailable(f"ml_inference_failed: {exc}")

    feature_names = feature_schema.get("feature_names") or []
    feature_importance = get_feature_importance(model, feature_names)
    explanation = build_prediction_explanation(
        feature_values=features,
        feature_importance=feature_importance,
        team_a_name=match.team_a.name,
        team_b_name=match.team_b.name,
    )

    return FormulaPredictionResponse(
        match_id=str(match.id),
        prediction_type="ml",
        model_version=active_version.version,
        team_a_probability=team_a_probability,
        team_b_probability=team_b_probability,
        confidence="medium",
        confidence_score=0.5,
        factors=PredictionFactors(
            recent_form=0.0,
            team_rating=0.0,
            head_to_head=0.0,
            hero_pool=0.0,
            roster_stability=0.0,
        ),
        explanation=explanation,
        warning=ML_PREDICTION_WARNING,
        fallback_used=False,
        fallback_reason=None,
        data_freshness={
            "features_generated_at": features_generated_at,
            "model_trained_at": active_version.trained_at.isoformat() if active_version.trained_at else None,
            "runtime_feature_adapter": runtime_adapter,
        },
    )


def _runtime_feature_adapter(
    active_feature_version: str,
    current_feature_version: str,
    feature_schema: dict[str, Any],
) -> str | None:
    if active_feature_version == current_feature_version:
        return None
    feature_names = feature_schema.get("feature_names") or []
    if (
        active_feature_version == "prematch_v3"
        and current_feature_version == "prematch_v4"
        and len(feature_names) == len(PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES)
        and set(feature_names) == PREMATCH_V3_DIFFERENTIAL_FEATURE_NAMES
    ):
        return "prematch_v3_from_prematch_v4_exact_differential_schema"
    return None


def _missing_schema_fields(
    features: dict[str, Any],
    feature_schema: dict[str, Any],
) -> list[str]:
    return sorted(name for name in (feature_schema.get("feature_names") or []) if name not in features)


def _get_active_model_version(db: Session) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


def _get_prematch_feature_record(
    db: Session,
    match_id: int,
    feature_version: str,
) -> MatchPrematchFeature | None:
    return db.scalar(
        select(MatchPrematchFeature)
        .where(
            MatchPrematchFeature.match_id == match_id,
            MatchPrematchFeature.feature_version == feature_version,
        )
        .order_by(MatchPrematchFeature.generated_at.desc(), MatchPrematchFeature.id.desc())
        .limit(1)
    )


def _active_feature_version(model_version: ModelVersion) -> str:
    artifact_metadata = model_version.artifact_metadata_json or {}
    value = artifact_metadata.get("feature_version")
    if isinstance(value, str) and value:
        return value
    metrics = model_version.metrics_json or {}
    value = metrics.get("feature_version")
    if isinstance(value, str) and value:
        return value
    dataset_metadata = metrics.get("dataset_metadata") or {}
    value = dataset_metadata.get("feature_version")
    if isinstance(value, str) and value:
        return value
    return "prematch_v3"


def _encode_features(features: dict[str, Any], feature_schema: dict[str, Any]) -> list[float]:
    feature_names = feature_schema.get("feature_names") or []
    categorical_maps = feature_schema.get("categorical_maps") or {}
    fill_values = feature_schema.get("fill_values") or {}
    return [
        _encode_value(name, features.get(name), categorical_maps.get(name, {}), fill_values.get(name, 0.0))
        for name in feature_names
    ]


def _encode_value(name: str, value: Any, category_map: dict[str, int], fill_value: float) -> float:
    if value is None:
        return float(fill_value)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(category_map.get(value, 0))
    return float(fill_value)


def _predict_team_a_probability(model: Any, encoded_features: list[float]) -> float:
    probabilities = model.predict_proba([encoded_features])
    return float(probabilities[0][1])


def _calibrate_probability(calibrator: Any, probability: float) -> float:
    calibrated = calibrator.predict_proba([probability])
    first = calibrated[0]
    if isinstance(first, (int, float)):
        return float(first)
    return float(first[1])


def _normalize_probabilities(team_a_probability: float) -> tuple[float, float]:
    team_a = min(0.9999, max(0.0001, float(team_a_probability)))
    team_b = 1.0 - team_a
    team_b = round(team_b, 4)
    team_a = round(1.0 - team_b, 4)
    return team_a, team_b
