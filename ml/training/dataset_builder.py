from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchPrematchFeature, Team
from ml.features.feature_schema import FEATURE_VERSION
from ml.config import ML_MAX_FEATURES, ML_MAX_TRAINING_ROWS
from ml.safety import assert_safe_training_profile
from worker.data_ingestion.pro_match_quality import is_verified_pro_tournament


FORBIDDEN_FEATURE_COLUMNS = {
    "winner_team_id",
    "result",
    "kills",
    "deaths",
    "duration",
    "team_a_probability",
    "team_b_probability",
}

NEUTRAL_FILL_VALUES = {
    "team_a_elo": 1500.0,
    "team_b_elo": 1500.0,
    "team_a_glicko": 1500.0,
    "team_b_glicko": 1500.0,
    "team_a_avg_opponent_elo_last_10": 1500.0,
    "team_b_avg_opponent_elo_last_10": 1500.0,
    "team_a_rating_uncertainty": 350.0,
    "team_b_rating_uncertainty": 350.0,
}

DIFFERENTIAL_FEATURES = {
    "elo_diff",
    "glicko_diff",
    "rating_uncertainty_diff",
    "form_diff_5",
    "form_diff_10",
    "form_diff_20",
    "recency_weighted_form_diff",
    "momentum_diff",
    "opponent_elo_diff_last_10",
    "strong_team_wins_diff",
    "weak_loss_diff",
    "h2h_matches_count",
    "h2h_team_a_winrate",
    "h2h_recent_weighted_score",
    "tournament_recent_winrate_diff",
    "bo3_winrate_diff",
    "bo5_winrate_diff",
    "roster_stability_diff",
    "same_roster_matches_diff",
    "current_patch_winrate_diff",
    "days_since_patch",
    "patch_recency_weight",
    "match_format",
    "is_playoff",
    "is_elimination_match",
}

DATASET_METADATA = {
    "feature_version": FEATURE_VERSION,
    "tier1_only": True,
    "training_profile": "tier1_only",
    "quality_scope": "verified_pro",
    "source": "local_postgresql",
    "feature_type": "prematch",
    "feature_set": "all",
    "synthetic_excluded": True,
}


class NotEnoughTrainingDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetRow:
    match_id: int
    start_time: datetime
    features: dict[str, Any]
    label: int
    sample_weight: float = 1.0


@dataclass(frozen=True)
class FeatureSchema:
    feature_names: list[str]
    categorical_maps: dict[str, dict[str, int]]
    fill_values: dict[str, float]


@dataclass(frozen=True)
class TrainingDataset:
    rows: list[DatasetRow]
    x: list[list[float]]
    y: list[int]
    sample_weights: list[float]
    feature_schema: FeatureSchema
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DatasetSplit:
    train: TrainingDataset
    validation: TrainingDataset
    test: TrainingDataset


def build_training_dataset(
    db: Session,
    *,
    min_rows: int = 1,
    training_profile: str = "tier1_only",
    feature_set: str = "all",
) -> TrainingDataset:
    metadata = _dataset_metadata(training_profile)
    if feature_set not in {"all", "differential"}:
        raise ValueError(f"Unknown feature set: {feature_set}")
    metadata["feature_set"] = feature_set
    assert_safe_training_profile(metadata)
    records = list(
        db.execute(
            select(Match, MatchPrematchFeature)
            .join(MatchPrematchFeature, MatchPrematchFeature.match_id == Match.id)
            .where(
                or_(
                    Match.is_tier1_match.is_(True),
                    and_(
                        Match.competition_tier == "pro",
                        Match.verification_status == "verified",
                        Match.is_training_eligible.is_(True),
                    ),
                ),
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                or_(Match.external_source.is_(None), Match.external_source != "dev_seed"),
                MatchPrematchFeature.feature_version == FEATURE_VERSION,
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
            .limit(ML_MAX_TRAINING_ROWS)
        ).all()
    )

    rows: list[DatasetRow] = []
    for match, prematch_features in records:
        if not match.tournament_name or not is_verified_pro_tournament(match.tournament_name):
            continue
        is_verified_pro_row = match.competition_tier == "pro"
        if is_verified_pro_row and training_profile == "tier1_only":
            continue
        if is_verified_pro_row and (
            match.verification_status != "verified" or match.is_training_eligible is not True
        ):
            continue
        if match.winner_team_id == match.team_a_id:
            label = 1
        elif match.winner_team_id == match.team_b_id:
            label = 0
        else:
            continue
        rows.append(
            DatasetRow(
                match_id=match.id,
                start_time=match.start_time,
                features=_select_feature_set(
                    remove_forbidden_feature_columns(prematch_features.features_json),
                    feature_set,
                ),
                label=label,
                sample_weight=0.5 if training_profile == "tier1_plus_verified_pro" and is_verified_pro_row else 1.0,
            )
        )

    if len(rows) < min_rows:
        raise NotEnoughTrainingDataError("Not enough eligible historical matches to train ML model.")

    return materialize_dataset(rows, metadata)


def remove_forbidden_feature_columns(features: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in features.items() if key not in FORBIDDEN_FEATURE_COLUMNS}


def materialize_dataset(rows: list[DatasetRow], metadata: dict[str, Any]) -> TrainingDataset:
    assert_safe_training_profile(metadata)
    feature_names = sorted({key for row in rows for key in row.features.keys() if key not in FORBIDDEN_FEATURE_COLUMNS})
    feature_names = feature_names[:ML_MAX_FEATURES]
    categorical_maps = _build_categorical_maps(rows, feature_names)
    fill_values = {name: _neutral_fill_value(name) for name in feature_names}
    schema = FeatureSchema(feature_names=feature_names, categorical_maps=categorical_maps, fill_values=fill_values)
    x = [encode_features(row.features, schema) for row in rows]
    y = [row.label for row in rows]
    sample_weights = [row.sample_weight for row in rows]
    return TrainingDataset(
        rows=rows,
        x=x,
        y=y,
        sample_weights=sample_weights,
        feature_schema=schema,
        metadata=dict(metadata),
    )


def split_time_based(dataset: TrainingDataset) -> DatasetSplit:
    row_count = len(dataset.rows)
    if row_count < 3:
        raise NotEnoughTrainingDataError("Not enough Tier 1 historical matches to train ML model.")

    train_end = max(1, int(row_count * 0.70))
    validation_end = max(train_end + 1, int(row_count * 0.85))
    validation_end = min(validation_end, row_count - 1)

    return DatasetSplit(
        train=_slice_dataset(dataset, 0, train_end),
        validation=_slice_dataset(dataset, train_end, validation_end),
        test=_slice_dataset(dataset, validation_end, row_count),
    )


def encode_features(features: dict[str, Any], schema: FeatureSchema) -> list[float]:
    return [_encode_value(name, features.get(name), schema) for name in schema.feature_names]


def _slice_dataset(dataset: TrainingDataset, start: int, end: int) -> TrainingDataset:
    rows = dataset.rows[start:end]
    x = dataset.x[start:end]
    y = dataset.y[start:end]
    return TrainingDataset(
        rows=rows,
        x=x,
        y=y,
        sample_weights=dataset.sample_weights[start:end],
        feature_schema=dataset.feature_schema,
        metadata=dataset.metadata,
    )


def _dataset_metadata(training_profile: str) -> dict[str, Any]:
    if training_profile == "tier1_only":
        return dict(DATASET_METADATA)
    if training_profile == "tier1_plus_verified_pro":
        return {
            **DATASET_METADATA,
            "tier1_only": False,
            "training_profile": training_profile,
            "verified_pro_only": True,
            "tier1_evaluation_required": True,
            "pro_sample_weight": 0.5,
        }
    raise ValueError(f"Unknown training profile: {training_profile}")


def _build_categorical_maps(rows: list[DatasetRow], feature_names: list[str]) -> dict[str, dict[str, int]]:
    maps: dict[str, dict[str, int]] = {}
    for name in feature_names:
        values = sorted({str(row.features[name]) for row in rows if isinstance(row.features.get(name), str)})
        if values:
            maps[name] = {value: index + 1 for index, value in enumerate(values)}
    return maps


def _encode_value(name: str, value: Any, schema: FeatureSchema) -> float:
    if value is None:
        return schema.fill_values.get(name, 0.0)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(schema.categorical_maps.get(name, {}).get(value, 0))
    return schema.fill_values.get(name, 0.0)


def _neutral_fill_value(name: str) -> float:
    if name in NEUTRAL_FILL_VALUES:
        return NEUTRAL_FILL_VALUES[name]
    if "winrate" in name or name in {"h2h_recent_weighted_score", "patch_recency_weight"}:
        return 0.5
    return 0.0


def _select_feature_set(features: dict[str, Any], feature_set: str) -> dict[str, Any]:
    if feature_set == "all":
        return features
    return {name: value for name, value in features.items() if name in DIFFERENTIAL_FEATURES}
