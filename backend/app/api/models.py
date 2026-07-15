from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Backtest, ModelVersion
from app.prediction.forecast_gap_report import build_forecast_gap_report
from app.prediction.forecast_tracker import build_prospective_report
from app.prediction.prospective_decision import build_prospective_decision


router = APIRouter(tags=["models"])
DRAFT_BACKTEST_REPORT_PATH = Path("ml/artifacts/draft_backtest_report.json")


@router.get("/models")
def list_models(db: Session = Depends(get_db)) -> list[dict]:
    return with_db_error_handling(
        lambda: [_model_to_dict(model) for model in db.scalars(select(ModelVersion).order_by(ModelVersion.trained_at.desc())).all()]
    )


@router.get("/models/active")
def get_active_model(db: Session = Depends(get_db)) -> dict | None:
    model = with_db_error_handling(
        lambda: db.scalar(
            select(ModelVersion)
            .where(ModelVersion.is_active.is_(True))
            .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )
    )
    return _model_to_dict(model) if model else None


@router.get("/models/candidates")
def get_candidate_models(db: Session = Depends(get_db)) -> list[dict]:
    return with_db_error_handling(
        lambda: [
            _model_to_dict(model)
            for model in db.scalars(
                select(ModelVersion)
                .where(ModelVersion.status == "candidate", ModelVersion.is_active.is_(False))
                .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
            ).all()
            if (model.artifact_metadata_json or {}).get("draft_aware") is not True
        ]
    )


@router.get("/models/draft-experiments")
def get_draft_experiments(db: Session = Depends(get_db)) -> dict:
    candidates = with_db_error_handling(
        lambda: [
            _model_to_dict(model)
            for model in db.scalars(
                select(ModelVersion)
                .where(ModelVersion.status == "candidate", ModelVersion.is_active.is_(False))
                .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
            ).all()
            if (model.artifact_metadata_json or {}).get("draft_aware") is True
        ]
    )
    latest_backtest = _read_draft_backtest_report()
    return {
        "status": latest_backtest.get("status", "missing") if latest_backtest else "missing",
        "draft_candidates": candidates,
        "latest_draft_backtest": latest_backtest,
        "sample_size": (latest_backtest or {}).get("sample_size"),
        "warnings": (latest_backtest or {}).get("warnings", []),
        "promotion_enabled": False,
        "not_used_in_main_prediction": True,
    }


@router.get("/models/prospective-accuracy")
def get_prospective_accuracy(db: Session = Depends(get_db)) -> dict:
    return with_db_error_handling(lambda: build_prospective_report(db))


@router.get("/models/prospective-decision")
def get_prospective_decision(db: Session = Depends(get_db)) -> dict:
    return with_db_error_handling(
        lambda: build_prospective_decision(build_prospective_report(db))
    )


@router.get("/models/forecast-health")
def get_forecast_health(db: Session = Depends(get_db)) -> dict:
    return with_db_error_handling(lambda: build_forecast_gap_report(db))


@router.get("/models/{model_id}/promotion-status")
def get_model_promotion_status(model_id: int, db: Session = Depends(get_db)) -> dict:
    model = with_db_error_handling(lambda: db.get(ModelVersion, model_id))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return {
        "id": model.id,
        "status": model.status,
        "is_active": model.is_active,
        "promoted_at": model.promoted_at,
        "rejected_at": model.rejected_at,
        "promotion_reason": model.promotion_reason,
        "dev_seed_warning": _dev_seed_warning(model),
    }


@router.get("/models/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db)) -> dict:
    model = with_db_error_handling(lambda: db.get(ModelVersion, model_id))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return _model_to_dict(model)


@router.get("/models/{model_id}/backtests")
def get_model_backtests(model_id: int, db: Session = Depends(get_db)) -> list[dict]:
    model = with_db_error_handling(lambda: db.get(ModelVersion, model_id))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return with_db_error_handling(
        lambda: [
            _backtest_to_dict(backtest)
            for backtest in db.scalars(
                select(Backtest).where(Backtest.model_version_id == model_id).order_by(Backtest.started_at.desc())
            ).all()
        ]
    )


@router.get("/backtests/latest")
def get_latest_backtest(db: Session = Depends(get_db)) -> dict | None:
    def _query_latest_active_backtest() -> Backtest | None:
        active_model = db.scalar(
            select(ModelVersion)
            .where(ModelVersion.is_active.is_(True))
            .order_by(ModelVersion.promoted_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )
        statement = select(Backtest)
        if active_model is not None:
            statement = statement.where(Backtest.model_version_id == active_model.id)
        return db.scalar(statement.order_by(Backtest.started_at.desc(), Backtest.id.desc()).limit(1))

    backtest = with_db_error_handling(_query_latest_active_backtest)
    return _backtest_to_dict(backtest) if backtest else None


def _model_to_dict(model: ModelVersion) -> dict:
    return {
        "id": model.id,
        "model_name": model.model_name,
        "model_type": model.model_type,
        "version": model.version,
        "trained_at": model.trained_at,
        "metrics_json": model.metrics_json,
        "artifact_path": model.artifact_path,
        "is_active": model.is_active,
        "status": model.status,
        "promoted_at": model.promoted_at,
        "rejected_at": model.rejected_at,
        "promotion_reason": model.promotion_reason,
        "artifact_metadata_json": model.artifact_metadata_json,
        "dev_seed_warning": _dev_seed_warning(model),
    }


def _backtest_to_dict(backtest: Backtest) -> dict:
    warning = None
    if backtest.dataset_type == "dev_seed":
        warning = "Dev seed data is synthetic and must not be used for real accuracy claims."
    return {
        "id": backtest.id,
        "model_version_id": backtest.model_version_id,
        "model_version": backtest.model_version.version if backtest.model_version else None,
        "model_status": backtest.model_version.status if backtest.model_version else None,
        "started_at": backtest.started_at,
        "finished_at": backtest.finished_at,
        "date_from": backtest.date_from,
        "date_to": backtest.date_to,
        "dataset_type": backtest.dataset_type,
        "matches_count": backtest.matches_count,
        "metrics_json": backtest.metrics_json,
        "report_path": backtest.report_path,
        "warning": warning,
    }


def _read_draft_backtest_report() -> dict | None:
    if not DRAFT_BACKTEST_REPORT_PATH.exists():
        return None
    try:
        return json.loads(DRAFT_BACKTEST_REPORT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "failed", "warnings": ["Draft backtest report is unreadable."], "sample_size": 0}


def _dev_seed_warning(model: ModelVersion) -> str | None:
    metadata = (model.metrics_json or {}).get("dataset_metadata") or {}
    if metadata.get("source") == "dev_seed" or metadata.get("dataset_type") == "dev_seed":
        return "Candidate was trained on synthetic dev_seed data; use only for local testing."
    return None
