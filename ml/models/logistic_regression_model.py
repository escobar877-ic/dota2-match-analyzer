from __future__ import annotations

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.config import ML_RANDOM_STATE
from ml.safety import assert_allowed_model


def create_logistic_regression_model() -> Pipeline:
    assert_allowed_model("logistic_regression")
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(max_iter=2000, random_state=ML_RANDOM_STATE),
            ),
        ]
    )
