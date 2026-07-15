from __future__ import annotations

import importlib.util
from pathlib import Path

from ml.config import (
    ML_ALLOWED_MODELS,
    ML_ARTIFACT_DIR,
    ML_CPU_ONLY,
    ML_FORBIDDEN_PACKAGES,
    ML_LOCAL_ONLY,
    ML_REQUIRE_TIER1_ONLY,
)


class MLSafetyError(ValueError):
    pass


def assert_local_ml_only() -> None:
    if not ML_LOCAL_ONLY:
        raise MLSafetyError("ML must run locally only. Cloud ML is not allowed.")
    if not ML_CPU_ONLY:
        raise MLSafetyError("ML must be CPU-first. GPU/cloud acceleration is not required or allowed.")


def assert_allowed_model(model_name: str) -> None:
    if model_name not in ML_ALLOWED_MODELS:
        allowed = ", ".join(ML_ALLOWED_MODELS)
        raise MLSafetyError(f"Model '{model_name}' is not allowed. Allowed models: {allowed}.")


def assert_tier1_training_only(dataset_metadata: dict) -> None:
    if not ML_REQUIRE_TIER1_ONLY:
        raise MLSafetyError("Tier 1-only training is required by project policy.")
    if dataset_metadata.get("tier1_only") is not True:
        raise MLSafetyError("Training dataset must be marked with tier1_only=true.")


def assert_safe_training_profile(dataset_metadata: dict) -> None:
    profile = dataset_metadata.get("training_profile", "tier1_only")
    if profile == "tier1_only":
        assert_tier1_training_only(dataset_metadata)
        return
    if profile != "tier1_plus_verified_pro":
        raise MLSafetyError(f"Unknown training profile: {profile}.")
    if dataset_metadata.get("verified_pro_only") is not True:
        raise MLSafetyError("Hybrid training may include verified professional matches only.")
    if dataset_metadata.get("tier1_evaluation_required") is not True:
        raise MLSafetyError("Hybrid training requires a Tier 1-only evaluation gate.")


def assert_no_forbidden_packages() -> None:
    installed = [package for package in ML_FORBIDDEN_PACKAGES if importlib.util.find_spec(package) is not None]
    if installed:
        raise MLSafetyError(f"Forbidden ML packages are installed or importable: {', '.join(installed)}.")


def artifact_dir_exists() -> bool:
    return Path(ML_ARTIFACT_DIR).is_dir()
