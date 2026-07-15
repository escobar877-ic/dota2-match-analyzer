from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import Any

from ml.config import ML_ARTIFACT_DIR


MODEL_ARTIFACT_PATH = Path(ML_ARTIFACT_DIR) / "prematch_model.pkl"
CALIBRATOR_ARTIFACT_PATH = Path(ML_ARTIFACT_DIR) / "calibrator.pkl"
FEATURE_SCHEMA_PATH = Path(ML_ARTIFACT_DIR) / "feature_schema.json"


def load_active_model() -> Any | None:
    if not MODEL_ARTIFACT_PATH.exists():
        return None
    return pickle.loads(_read_bytes_with_retry(MODEL_ARTIFACT_PATH))


def load_feature_schema() -> dict[str, Any] | None:
    if not FEATURE_SCHEMA_PATH.exists():
        return None
    return json.loads(_read_bytes_with_retry(FEATURE_SCHEMA_PATH).decode("utf-8"))


def load_calibrator() -> Any | None:
    if not CALIBRATOR_ARTIFACT_PATH.exists():
        return None
    return pickle.loads(_read_bytes_with_retry(CALIBRATOR_ARTIFACT_PATH))


def model_artifacts_exist() -> bool:
    return MODEL_ARTIFACT_PATH.exists() and FEATURE_SCHEMA_PATH.exists()


def _read_bytes_with_retry(path: Path, attempts: int = 10) -> bytes:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            return path.read_bytes()
        except OSError as exc:
            last_error = exc
            if getattr(exc, "errno", None) != 35 or attempt == attempts - 1:
                raise
            time.sleep(0.2)
    if last_error is not None:
        raise last_error
    raise FileNotFoundError(path)
