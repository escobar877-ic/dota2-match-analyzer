from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db.models import Backtest, ModelVersion
from ml.config import ML_ARTIFACT_DIR
from ml.models.model_loader import CALIBRATOR_ARTIFACT_PATH, FEATURE_SCHEMA_PATH, MODEL_ARTIFACT_PATH


DATA_COVERAGE_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "data_coverage_report.json"
ACCEPTABLE_CALIBRATION_ERROR = 0.12


def get_active_model_version(db: Session) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


def get_candidate_model_versions(db: Session) -> list[ModelVersion]:
    return list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.status == "candidate", ModelVersion.is_active.is_(False))
            .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        ).all()
    )


def get_latest_backtest_metrics(db: Session, model_version_id: int | None = None) -> Backtest | None:
    query = select(Backtest)
    if model_version_id is not None:
        query = query.where(Backtest.model_version_id == model_version_id)
    return db.scalar(query.order_by(Backtest.started_at.desc(), Backtest.id.desc()).limit(1))


def compare_candidate_to_active(candidate: ModelVersion, active: ModelVersion | None, latest_backtest: Backtest | None) -> dict:
    candidate_metrics, active_metrics = _same_window_backtest_metrics(latest_backtest)
    if candidate_metrics:
        candidate_metrics["training_completed"] = True
        candidate_metrics["artifacts_exist"] = _artifacts_exist(candidate)
        candidate_metrics["dataset_type"] = _dataset_type_from_model(candidate)
    else:
        candidate_metrics = _model_metrics(candidate)
    if not active_metrics:
        active_metrics = _model_metrics(active) if active else {}
    dataset_type = latest_backtest.dataset_type if latest_backtest else _dataset_type_from_model(candidate)
    decision = should_promote_candidate(candidate_metrics, active_metrics, dataset_type)
    return {
        "candidate_id": candidate.id,
        "active_id": active.id if active else None,
        "dataset_type": dataset_type,
        "candidate_metrics": candidate_metrics,
        "active_metrics": active_metrics,
        **decision,
    }


def should_promote_candidate(candidate_metrics: dict, active_metrics: dict | None, dataset_type: str | None) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    dataset_type = dataset_type or candidate_metrics.get("dataset_type") or "unknown"

    if dataset_type == "dev_seed":
        warnings.append("Synthetic dev seed metrics are for local testing only and are not real accuracy.")

    if not candidate_metrics:
        reasons.append("Candidate metrics are missing.")
    if candidate_metrics.get("training_completed") is False:
        reasons.append("Training has not completed.")
    if candidate_metrics.get("artifacts_exist") is False:
        reasons.append("Candidate artifacts are missing.")

    candidate_log_loss = _metric(candidate_metrics, "log_loss")
    candidate_brier = _metric(candidate_metrics, "brier_score")
    candidate_calibration = _metric(candidate_metrics, "calibration_error")
    if candidate_log_loss is None:
        reasons.append("Candidate log_loss is missing.")
    if candidate_brier is None:
        reasons.append("Candidate brier_score is missing.")
    if candidate_calibration is not None and candidate_calibration > ACCEPTABLE_CALIBRATION_ERROR:
        reasons.append("Candidate calibration_error is too high.")

    active_metrics = active_metrics or {}
    active_log_loss = _metric(active_metrics, "log_loss")
    active_brier = _metric(active_metrics, "brier_score")
    if active_log_loss is not None and candidate_log_loss is not None and candidate_log_loss > active_log_loss:
        reasons.append("Candidate log_loss is worse than active model.")
    if active_brier is not None and candidate_brier is not None and candidate_brier > active_brier:
        reasons.append("Candidate brier_score is worse than active model.")

    return {
        "should_promote": not reasons,
        "reasons": reasons,
        "warnings": warnings,
    }


def promote_model_version(
    model_version_id: int,
    reason: str,
    *,
    db: Session | None = None,
    force: bool = False,
    dev_allow_synthetic_promotion: bool = False,
) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        model = db.get(ModelVersion, model_version_id)
        if model is None:
            raise ValueError(f"Model version {model_version_id} not found.")
        dataset_type = _dataset_type_from_model(model)
        if dataset_type == "dev_seed" and not (dev_allow_synthetic_promotion or force):
            raise ValueError("Dev seed promotion requires --dev-allow-synthetic-promotion or --force-promote.")
        if not force:
            _assert_artifacts_exist(model)
        _copy_artifacts_to_active(model)
        now = datetime.now(timezone.utc)
        db.execute(update(ModelVersion).where(ModelVersion.is_active.is_(True)).values(is_active=False, status="archived"))
        model.is_active = True
        model.status = "active"
        model.promoted_at = now
        model.rejected_at = None
        model.promotion_reason = reason
        db.commit()
        return {"promoted": True, "model_version_id": model.id, "warning": _dev_seed_warning(dataset_type)}
    finally:
        if owns_session:
            db.close()


def reject_model_version(model_version_id: int, reason: str, *, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        model = db.get(ModelVersion, model_version_id)
        if model is None:
            raise ValueError(f"Model version {model_version_id} not found.")
        model.status = "rejected"
        model.is_active = False
        model.rejected_at = datetime.now(timezone.utc)
        model.promotion_reason = reason
        db.commit()
        return {"rejected": True, "model_version_id": model.id}
    finally:
        if owns_session:
            db.close()


def archive_old_models(keep_last: int = 5, *, db: Session | None = None) -> int:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        old_models = list(
            db.scalars(
                select(ModelVersion)
                .where(ModelVersion.is_active.is_(False), ModelVersion.status != "archived")
                .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
                .offset(max(0, keep_last))
            ).all()
        )
        for model in old_models:
            if model.status == "candidate":
                model.status = "archived"
        db.commit()
        return sum(1 for model in old_models if model.status == "archived")
    finally:
        if owns_session:
            db.close()


def auto_promote_if_better(*, dev_allow_synthetic_promotion: bool = False, force: bool = False) -> dict:
    db = SessionLocal()
    try:
        active = get_active_model_version(db)
        candidates = get_candidate_model_versions(db)
        if not candidates:
            return {"promoted": False, "reason": "No candidate models found."}
        candidate = candidates[0]
        latest_backtest = get_latest_backtest_metrics(db, candidate.id)
        if latest_backtest is None and not force:
            return {"promoted": False, "reason": "Backtest is required before auto-promotion."}
        dataset_type = latest_backtest.dataset_type if latest_backtest else _dataset_type_from_model(candidate)
        coverage_gate = _real_coverage_gate(
            dataset_type,
            candidate=candidate,
            latest_backtest=latest_backtest,
        )
        if coverage_gate is not None and not force:
            return {"promoted": False, "reason": coverage_gate}
        if dataset_type == "dev_seed" and not (dev_allow_synthetic_promotion or force):
            return {"promoted": False, "reason": "Dev seed auto-promotion requires explicit dev flag."}
        comparison = compare_candidate_to_active(candidate, active, latest_backtest)
        if not comparison["should_promote"] and not force:
            return {"promoted": False, "reason": "; ".join(comparison["reasons"]), "comparison": comparison}
        reason = "auto-promote-if-better"
        if dataset_type == "dev_seed":
            reason += "; synthetic dev seed, not real accuracy"
        result = promote_model_version(
            candidate.id,
            reason,
            db=db,
            force=force,
            dev_allow_synthetic_promotion=dev_allow_synthetic_promotion,
        )
        result["comparison"] = comparison
        return result
    finally:
        db.close()


def _model_metrics(model: ModelVersion | None) -> dict:
    if model is None:
        return {}
    report = model.metrics_json or {}
    metrics = dict(report.get("test_metrics") or report.get("metrics") or {})
    metrics["training_completed"] = True
    metrics["artifacts_exist"] = _artifacts_exist(model)
    metrics["dataset_type"] = _dataset_type_from_model(model)
    return metrics


def _same_window_backtest_metrics(latest_backtest: Backtest | None) -> tuple[dict, dict]:
    if latest_backtest is None:
        return {}, {}
    report = latest_backtest.metrics_json or {}
    candidate_metrics = dict((report.get("models") or {}).get("ml") or {})
    active_metrics = dict((report.get("active_ml_comparison") or {}).get("metrics") or {})
    return candidate_metrics, active_metrics


def _metric(metrics: dict, name: str) -> float | None:
    value = metrics.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _dataset_type_from_model(model: ModelVersion) -> str:
    metadata = (model.metrics_json or {}).get("dataset_metadata") or {}
    if metadata.get("source") == "dev_seed":
        return "dev_seed"
    sources = metadata.get("sources")
    if sources == ["dev_seed"] or sources == {"dev_seed": 1}:
        return "dev_seed"
    return str(metadata.get("dataset_type") or "unknown")


def _real_coverage_gate(
    dataset_type: str,
    *,
    candidate: ModelVersion,
    latest_backtest: Backtest | None,
) -> str | None:
    if dataset_type == "dev_seed":
        return None
    if not DATA_COVERAGE_REPORT_PATH.exists():
        return "Data coverage report is missing for real auto-promotion."
    try:
        coverage = json.loads(DATA_COVERAGE_REPORT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "Data coverage report is unreadable for real auto-promotion."

    readiness = str(coverage.get("training_readiness") or "insufficient")
    if readiness == "insufficient":
        return "Data coverage readiness is insufficient for real auto-promotion."

    real_rows = int(coverage.get("real_tier1_historical_matches_count") or 0)
    if real_rows < 300:
        return "Real auto-promotion requires at least 300 real Tier 1 historical matches."

    candidate_metadata = (candidate.metrics_json or {}).get("dataset_metadata") or {}
    if int(candidate_metadata.get("dev_seed_rows_count") or 0) > 0:
        return "Candidate training dataset includes dev_seed rows."

    backtest_report = (latest_backtest.metrics_json or {}) if latest_backtest else {}
    if int(backtest_report.get("dev_seed_rows_count") or 0) > 0:
        return "Candidate backtest includes dev_seed rows."
    if latest_backtest and latest_backtest.dataset_type in {"mixed", "dev_seed"}:
        return "Candidate backtest must use real data without dev_seed rows."

    return None


def _coverage_readiness() -> str:
    if not DATA_COVERAGE_REPORT_PATH.exists():
        return "insufficient"
    try:
        return str(json.loads(DATA_COVERAGE_REPORT_PATH.read_text(encoding="utf-8")).get("training_readiness") or "insufficient")
    except json.JSONDecodeError:
        return "insufficient"


def _artifacts_exist(model: ModelVersion) -> bool:
    metadata = model.artifact_metadata_json or {}
    paths = [model.artifact_path, metadata.get("feature_schema_path")]
    calibrator_path = metadata.get("calibrator_path")
    if calibrator_path:
        paths.append(calibrator_path)
    return all(path and Path(path).exists() for path in paths)


def _assert_artifacts_exist(model: ModelVersion) -> None:
    if not _artifacts_exist(model):
        raise ValueError("Candidate artifacts are missing.")


def _copy_artifacts_to_active(model: ModelVersion) -> None:
    metadata = model.artifact_metadata_json or {}
    model_path = Path(model.artifact_path)
    schema_path = Path(metadata.get("feature_schema_path") or "")
    calibrator_value = metadata.get("calibrator_path")
    calibrator_path = Path(calibrator_value) if calibrator_value else None
    MODEL_ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model_path, MODEL_ARTIFACT_PATH)
    shutil.copyfile(schema_path, FEATURE_SCHEMA_PATH)
    if calibrator_path is not None and calibrator_path.is_file():
        shutil.copyfile(calibrator_path, CALIBRATOR_ARTIFACT_PATH)
    elif CALIBRATOR_ARTIFACT_PATH.exists():
        CALIBRATOR_ARTIFACT_PATH.unlink()


def _dev_seed_warning(dataset_type: str) -> str | None:
    if dataset_type == "dev_seed":
        return "Promoted from synthetic dev seed metrics for local testing only; not real accuracy."
    return None


def _list_models() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        return [
            {
                "id": model.id,
                "version": model.version,
                "model_name": model.model_name,
                "status": model.status,
                "is_active": model.is_active,
                "trained_at": model.trained_at.isoformat() if model.trained_at else None,
                "promoted_at": model.promoted_at.isoformat() if model.promoted_at else None,
                "rejected_at": model.rejected_at.isoformat() if model.rejected_at else None,
                "promotion_reason": model.promotion_reason,
            }
            for model in db.scalars(select(ModelVersion).order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())).all()
        ]
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely promote or reject local prematch model versions.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--promote", type=int)
    parser.add_argument("--reject", type=int)
    parser.add_argument("--reason")
    parser.add_argument("--auto-promote-if-better", action="store_true")
    parser.add_argument("--dev-allow-synthetic-promotion", action="store_true")
    parser.add_argument("--force-promote", action="store_true")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(_list_models(), indent=2, default=str))
        return
    if args.promote is not None:
        if not args.reason:
            raise SystemExit("--reason is required for promotion.")
        result = promote_model_version(
            args.promote,
            args.reason,
            force=args.force_promote,
            dev_allow_synthetic_promotion=args.dev_allow_synthetic_promotion,
        )
        print(json.dumps(result, indent=2, default=str))
        return
    if args.reject is not None:
        if not args.reason:
            raise SystemExit("--reason is required for rejection.")
        print(json.dumps(reject_model_version(args.reject, args.reason), indent=2, default=str))
        return
    if args.auto_promote_if_better:
        print(
            json.dumps(
                auto_promote_if_better(
                    dev_allow_synthetic_promotion=args.dev_allow_synthetic_promotion,
                    force=args.force_promote,
                ),
                indent=2,
                default=str,
            )
        )
        return
    parser.print_help()


if __name__ == "__main__":
    main()
