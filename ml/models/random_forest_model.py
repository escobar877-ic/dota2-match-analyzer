from __future__ import annotations

from sklearn.ensemble import RandomForestClassifier

from ml.config import ML_RANDOM_STATE
from ml.safety import assert_allowed_model


def create_random_forest_model(n_jobs: int = 1) -> RandomForestClassifier:
    assert_allowed_model("random_forest")
    return RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        n_jobs=n_jobs,
        random_state=ML_RANDOM_STATE,
    )
