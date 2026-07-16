from __future__ import annotations

from sklearn.ensemble import ExtraTreesClassifier

from ml.config import ML_RANDOM_STATE
from ml.safety import assert_allowed_model


def create_extra_trees_model(n_jobs: int = 1) -> ExtraTreesClassifier:
    assert_allowed_model("extra_trees")
    return ExtraTreesClassifier(
        n_estimators=500,
        max_depth=7,
        min_samples_leaf=4,
        max_features="sqrt",
        class_weight="balanced",
        n_jobs=n_jobs,
        random_state=ML_RANDOM_STATE,
    )
