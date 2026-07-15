from __future__ import annotations

from ml.features.draft_features import DRAFT_SAFE_DEFAULTS
from ml.features.feature_schema import ALL_FEATURE_FIELDS as PREMATCH_V3_FEATURE_FIELDS


FEATURE_VERSION = "draft_v1"

DRAFT_FEATURE_FIELDS = list(DRAFT_SAFE_DEFAULTS.keys())

ALL_FEATURE_FIELDS = list(PREMATCH_V3_FEATURE_FIELDS) + DRAFT_FEATURE_FIELDS

SAFE_DEFAULTS = {
    **{field: None for field in PREMATCH_V3_FEATURE_FIELDS},
    **DRAFT_SAFE_DEFAULTS,
}


def assert_complete_feature_set(features: dict) -> None:
    missing = [field for field in ALL_FEATURE_FIELDS if field not in features]
    if missing:
        raise ValueError(f"Missing draft-aware feature fields: {', '.join(missing)}")
