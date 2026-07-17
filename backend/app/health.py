from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ModelVersion
from ml.config import ML_ARTIFACT_DIR


REFRESH_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "prediction_refresh_report.json"
COVERAGE_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "data_coverage_report.json"
LIVE_CONTEXT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "live_match_context_report.json"


def build_system_readiness(
    db: Session,
    *,
    now: datetime | None = None,
    refresh_report_path: Path | None = None,
    coverage_report_path: Path | None = None,
    live_context_report_path: Path | None = None,
) -> dict[str, Any]:
    now = _aware(now or datetime.now(timezone.utc))
    refresh_path = refresh_report_path or REFRESH_REPORT_PATH
    coverage_path = coverage_report_path or COVERAGE_REPORT_PATH
    live_context_path = live_context_report_path or LIVE_CONTEXT_REPORT_PATH

    db.execute(select(1)).scalar_one()
    active_model = db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.promoted_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )

    model_check = _model_check(active_model)
    scheduler_check = _scheduler_check(refresh_path, now)
    live_context_check = _live_context_check(live_context_path, now)
    coverage_check = _coverage_check(coverage_path)
    checks = {
        "database": {"status": "ok"},
        "active_model": model_check,
        "forecast_scheduler": scheduler_check,
        "live_context_scheduler": live_context_check,
        "data_coverage": coverage_check,
    }
    degraded = any(check["status"] != "ok" for check in checks.values())
    return {
        "status": "warning" if degraded else "ok",
        "ready": True,
        "generated_at": now.isoformat(),
        "service": "dota-analyzer-backend",
        "checks": checks,
        "active_model_version": model_check.get("version"),
        "scheduler_age_minutes": scheduler_check.get("age_minutes"),
        "live_context_age_minutes": live_context_check.get("age_minutes"),
        "real_tier1_matches": coverage_check.get("real_tier1_matches"),
        "verified_pro_matches": coverage_check.get("verified_pro_matches"),
        "warnings": [
            str(check["message"])
            for check in checks.values()
            if check["status"] != "ok" and check.get("message")
        ],
    }


def _model_check(model: ModelVersion | None) -> dict[str, Any]:
    if model is None:
        return {
            "status": "warning",
            "message": "No active ML model; formula/Elo fallback remains available.",
            "fallback_available": True,
        }
    artifact = Path(model.artifact_path) if model.artifact_path else None
    exists = bool(artifact and artifact.is_file() and artifact.stat().st_size > 0)
    return {
        "status": "ok" if exists else "warning",
        "model_id": model.id,
        "version": model.version,
        "artifact_exists": exists,
        "fallback_available": True,
        "message": None if exists else "Active model artifact is missing; formula/Elo fallback will be used.",
    }


def _scheduler_check(path: Path, now: datetime) -> dict[str, Any]:
    report = _read_json(path)
    if report is None:
        return {
            "status": "warning",
            "message": "Forecast refresh report is missing.",
            "report_path": str(path),
        }
    generated_at = _parse_datetime(report.get("generated_at"))
    if generated_at is None:
        return {
            "status": "warning",
            "message": "Forecast refresh report has no valid generated_at timestamp.",
            "report_path": str(path),
        }
    age_minutes = max(0.0, (now - generated_at).total_seconds() / 60.0)
    stale_after = max(15, int(os.getenv("FORECAST_REFRESH_STALE_MINUTES", "45")))
    is_fresh = age_minutes <= stale_after
    cycle_status = str(report.get("cycle_status") or report.get("status") or "missing")
    cycle_failed = cycle_status == "failed"
    cycle_warning = cycle_status == "warning"
    ok = is_fresh and not cycle_failed and not cycle_warning
    if cycle_failed:
        message = "Latest forecast refresh failed."
    elif cycle_warning:
        message = "Latest forecast refresh completed with warnings."
    elif not is_fresh:
        message = f"Forecast refresh is stale ({age_minutes:.0f} minutes old)."
    else:
        message = None
    return {
        "status": "ok" if ok else "warning",
        "last_refresh_status": report.get("status"),
        "last_cycle_status": cycle_status,
        "generated_at": generated_at.isoformat(),
        "age_minutes": round(age_minutes, 1),
        "stale_after_minutes": stale_after,
        "message": message,
    }


def _coverage_check(path: Path) -> dict[str, Any]:
    report = _read_json(path)
    if report is None:
        return {
            "status": "warning",
            "message": "Data coverage report is missing.",
            "report_path": str(path),
        }
    real_rows = int(report.get("real_tier1_historical_matches_count") or 0)
    verified_pro_rows = int(report.get("verified_pro_historical_matches_count") or 0)
    readiness = str(report.get("training_readiness") or "unknown")
    return {
        "status": "ok" if real_rows >= 300 else "warning",
        "training_readiness": readiness,
        "real_tier1_matches": real_rows,
        "verified_pro_matches": verified_pro_rows,
        "patch_coverage_ratio": report.get("patch_coverage_ratio"),
        "roster_coverage_ratio": report.get("roster_coverage_ratio"),
        "message": None if real_rows >= 300 else "Fewer than 300 real strict Tier 1 matches are available.",
    }


def _live_context_check(path: Path, now: datetime) -> dict[str, Any]:
    report = _read_json(path)
    if report is None:
        return {
            "status": "warning",
            "message": "Live context refresh report is missing.",
            "report_path": str(path),
        }
    generated_at = _parse_datetime(report.get("generated_at"))
    if generated_at is None:
        return {
            "status": "warning",
            "message": "Live context refresh report has no valid generated_at timestamp.",
            "report_path": str(path),
        }
    age_minutes = max(0.0, (now - generated_at).total_seconds() / 60.0)
    stale_after = max(2, int(os.getenv("LIVE_CONTEXT_STALE_MINUTES", "5")))
    report_status = str(report.get("status") or "missing").lower()
    is_fresh = age_minutes <= stale_after
    ok = is_fresh and report_status == "ok"
    if report_status == "failed":
        message = "Latest live context refresh failed."
    elif report_status == "warning":
        message = "Latest live context refresh completed with warnings."
    elif not is_fresh:
        message = f"Live context refresh is stale ({age_minutes:.0f} minutes old)."
    else:
        message = None
    return {
        "status": "ok" if ok else "warning",
        "last_refresh_status": report_status,
        "generated_at": generated_at.isoformat(),
        "age_minutes": round(age_minutes, 1),
        "stale_after_minutes": stale_after,
        "matched_live_matches": int(report.get("matched_live_matches") or 0),
        "drafts_available": int(report.get("drafts_available") or 0),
        "message": message,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
