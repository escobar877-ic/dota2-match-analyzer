from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, PredictionForecast
from app.prediction.forecast_tracker import (
    HORIZON_ORDER,
    SCHEDULE_DRIFT_MINUTES,
    horizon_bucket_for_lead_time,
)
from ml.config import ML_ARTIFACT_DIR


FORECAST_GAP_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "forecast_gap_report.json"
PREDICTION_REFRESH_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "prediction_refresh_report.json"


def build_forecast_gap_report(
    db,
    *,
    hours_ahead: int = 168,
    now: datetime | None = None,
    artifact_path: str | Path | None = FORECAST_GAP_REPORT_PATH,
    refresh_report_path: str | Path | None = PREDICTION_REFRESH_REPORT_PATH,
    history_days: int = 30,
    max_refresh_age_minutes: int | None = None,
) -> dict[str, Any]:
    now = _ensure_aware(now or datetime.now(timezone.utc))
    warnings: list[str] = []
    errors: list[str] = []

    upcoming_matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(
                Match.status == "upcoming",
                Match.is_tier1_match.is_(True),
                Match.external_source == "pandascore",
                Match.is_prediction_eligible.is_(True),
                Match.start_time > now,
                Match.start_time <= now + timedelta(hours=max(1, hours_ahead)),
            )
            .order_by(Match.start_time, Match.id)
        ).all()
    )
    forecasts_by_match = _forecast_horizons_by_match(db, [match.id for match in upcoming_matches])
    missing_snapshots = []
    for match in upcoming_matches:
        lead_time_hours = (_ensure_aware(match.start_time) - now).total_seconds() / 3600
        expected_horizon = horizon_bucket_for_lead_time(lead_time_hours)
        if expected_horizon is None:
            continue
        existing_horizons = forecasts_by_match.get(match.id, set())
        if expected_horizon not in existing_horizons:
            severity = "error" if expected_horizon == "final" else "warning"
            item = _missing_snapshot_item(match, lead_time_hours, expected_horizon, existing_horizons, severity)
            missing_snapshots.append(item)
            message = (
                f"Missing {expected_horizon} forecast for match {match.id} "
                f"({match.team_a.name} vs {match.team_b.name})."
            )
            if severity == "error":
                errors.append(message)
            else:
                warnings.append(message)

    settlement_gaps = _find_settlement_gaps(db)
    if settlement_gaps:
        warnings.append(f"{len(settlement_gaps)} pending forecast(s) can be settled after result sync.")

    historical_final_gaps, tracked_finished_matches = _find_historical_final_gaps(
        db,
        now=now,
        history_days=history_days,
    )
    if historical_final_gaps:
        warnings.append(
            f"{len(historical_final_gaps)} tracked finished match(es) missed the final forecast horizon."
        )

    schedule_drift_gaps = _find_schedule_drift_gaps(db)
    if schedule_drift_gaps:
        warnings.append(
            f"{len(schedule_drift_gaps)} forecast horizon(s) use an outdated scheduled start."
        )

    refresh_report = _read_refresh_report(refresh_report_path)
    refresh_status = (
        refresh_report.get("cycle_status") or refresh_report.get("status")
        if isinstance(refresh_report, dict)
        else "missing"
    )
    refresh_age_minutes = _refresh_age_minutes(refresh_report, now)
    stale_after = max_refresh_age_minutes or int(
        os.getenv("FORECAST_REFRESH_STALE_MINUTES", "45")
    )
    refresh_stale = refresh_age_minutes is not None and refresh_age_minutes > stale_after
    if refresh_status == "missing":
        warnings.append("No prediction refresh report found; start forecast-scheduler or run it once.")
    elif refresh_stale:
        warnings.append(
            f"Prediction refresh is stale ({refresh_age_minutes:.1f} minutes old; limit {stale_after} minutes)."
        )

    checks = {
        "current_horizon_snapshots": "ok" if not missing_snapshots else "warning",
        "final_horizon_snapshots": "failed" if any(item["severity"] == "error" for item in missing_snapshots) else "ok",
        "historical_final_coverage": "warning" if historical_final_gaps else "ok",
        "schedule_integrity": "warning" if schedule_drift_gaps else "ok",
        "settlement": "warning" if settlement_gaps else "ok",
        "refresh_report": "ok" if refresh_status not in {"missing", "failed"} else "warning",
        "scheduler_freshness": "warning" if refresh_stale else "ok",
    }
    status = "failed" if errors else ("warning" if warnings else "ok")
    report = {
        "status": status,
        "generated_at": now.isoformat(),
        "summary": {
            "upcoming_prediction_eligible": len(upcoming_matches),
            "missing_current_horizon_snapshots": len(missing_snapshots),
            "missing_final_snapshots": sum(1 for item in missing_snapshots if item["missing_horizon"] == "final"),
            "tracked_finished_matches": tracked_finished_matches,
            "historical_missing_final_snapshots": len(historical_final_gaps),
            "schedule_drift_forecasts": len(schedule_drift_gaps),
            "pending_settlement_gaps": len(settlement_gaps),
            "refresh_status": refresh_status,
            "refresh_age_minutes": round(refresh_age_minutes, 1) if refresh_age_minutes is not None else None,
            "refresh_stale": refresh_stale,
            "refresh_stale_after_minutes": stale_after,
        },
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "missing_snapshots": missing_snapshots[:50],
        "historical_final_gaps": historical_final_gaps[:50],
        "schedule_drift_gaps": schedule_drift_gaps[:50],
        "settlement_gaps": settlement_gaps[:50],
        "latest_refresh": refresh_report,
        "command_hints": [
            "docker compose up -d forecast-scheduler",
            "docker compose run --rm forecast-scheduler python -m worker.data_ingestion.prediction_refresh_scheduler --once",
            "docker compose run --rm backend python -m app.prediction.forecast_gap_report",
        ],
    }
    if artifact_path is not None:
        _write_json(report, Path(artifact_path))
    return report


def write_prediction_refresh_report(
    report: dict[str, Any],
    *,
    artifact_path: str | Path | None = PREDICTION_REFRESH_REPORT_PATH,
) -> None:
    if artifact_path is not None:
        _write_json(report, Path(artifact_path))


def _forecast_horizons_by_match(db, match_ids: list[int]) -> dict[int, set[str]]:
    if not match_ids:
        return {}
    rows = db.execute(
        select(PredictionForecast.match_id, PredictionForecast.horizon_bucket).where(
            PredictionForecast.match_id.in_(match_ids)
        )
    ).all()
    result: dict[int, set[str]] = {}
    for match_id, horizon in rows:
        result.setdefault(match_id, set()).add(horizon)
    return result


def _find_settlement_gaps(db) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(PredictionForecast)
            .options(
                selectinload(PredictionForecast.match).selectinload(Match.team_a),
                selectinload(PredictionForecast.match).selectinload(Match.team_b),
            )
            .join(Match, Match.id == PredictionForecast.match_id)
            .where(
                PredictionForecast.status == "pending",
                Match.status == "finished",
                (Match.is_draw.is_(True)) | (Match.winner_team_id.is_not(None)),
            )
            .order_by(PredictionForecast.generated_at)
        ).all()
    )
    return [
        {
            "forecast_id": forecast.id,
            "match_id": forecast.match_id,
            "horizon_bucket": forecast.horizon_bucket,
            "teams": _teams_label(forecast.match),
            "scheduled_start": _iso(forecast.scheduled_start),
            "status": forecast.status,
            "command_hint": "docker compose run --rm backend python -m app.prediction.forecast_tracker --settle",
        }
        for forecast in rows
    ]


def _find_historical_final_gaps(
    db,
    *,
    now: datetime,
    history_days: int,
) -> tuple[list[dict[str, Any]], int]:
    matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .join(PredictionForecast, PredictionForecast.match_id == Match.id)
            .where(
                Match.status == "finished",
                Match.is_tier1_match.is_(True),
                Match.external_source == "pandascore",
                Match.start_time >= now - timedelta(days=max(1, history_days)),
                Match.start_time <= now,
            )
            .distinct()
            .order_by(Match.start_time.desc(), Match.id.desc())
        ).all()
    )
    if not matches:
        return [], 0
    final_match_ids = set(
        db.scalars(
            select(PredictionForecast.match_id).where(
                PredictionForecast.match_id.in_([match.id for match in matches]),
                PredictionForecast.horizon_bucket == "final",
                PredictionForecast.is_primary.is_(True),
            )
        ).all()
    )
    gaps = [
        {
            "match_id": match.id,
            "external_id": match.external_id,
            "teams": _teams_label(match),
            "tournament": match.tournament_name,
            "start_time": _iso(match.start_time),
            "reason": "No primary final forecast was recorded within two hours of match start.",
        }
        for match in matches
        if match.id not in final_match_ids
    ]
    return gaps, len(matches)


def _find_schedule_drift_gaps(db) -> list[dict[str, Any]]:
    forecasts = list(
        db.scalars(
            select(PredictionForecast)
            .options(
                selectinload(PredictionForecast.match).selectinload(Match.team_a),
                selectinload(PredictionForecast.match).selectinload(Match.team_b),
            )
            .join(Match, Match.id == PredictionForecast.match_id)
            .where(Match.start_time.is_not(None))
            .order_by(PredictionForecast.generated_at.desc())
        ).all()
    )
    grouped: dict[tuple[int, str], list[PredictionForecast]] = {}
    for forecast in forecasts:
        grouped.setdefault((forecast.match_id, forecast.horizon_bucket), []).append(forecast)

    gaps = []
    for (match_id, horizon), rows in grouped.items():
        current_start = _ensure_aware(rows[0].match.start_time)
        if any(
            abs((_ensure_aware(row.scheduled_start) - current_start).total_seconds()) / 60
            <= SCHEDULE_DRIFT_MINUTES
            for row in rows
        ):
            continue
        newest = rows[0]
        drift_minutes = abs(
            (_ensure_aware(newest.scheduled_start) - current_start).total_seconds()
        ) / 60
        gaps.append(
            {
                "match_id": match_id,
                "horizon_bucket": horizon,
                "teams": _teams_label(newest.match),
                "forecast_scheduled_start": _iso(newest.scheduled_start),
                "current_scheduled_start": _iso(current_start),
                "drift_minutes": round(drift_minutes, 1),
                "reason": "No forecast revision exists for the current scheduled start.",
            }
        )
    return gaps


def _missing_snapshot_item(
    match: Match,
    lead_time_hours: float,
    expected_horizon: str,
    existing_horizons: set[str],
    severity: str,
) -> dict[str, Any]:
    return {
        "match_id": match.id,
        "external_id": match.external_id,
        "teams": _teams_label(match),
        "tournament": match.tournament_name,
        "start_time": _iso(match.start_time),
        "lead_time_hours": round(lead_time_hours, 2),
        "missing_horizon": expected_horizon,
        "existing_horizons": [horizon for horizon in HORIZON_ORDER if horizon in existing_horizons],
        "severity": severity,
        "command_hint": "docker compose run --rm forecast-scheduler python -m worker.data_ingestion.prediction_refresh_scheduler --once",
    }


def _read_refresh_report(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "not_checked"}
    target = Path(path)
    if not target.exists():
        return {"status": "missing", "message": "Run forecast-scheduler to generate prediction_refresh_report.json."}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "failed", "message": f"Prediction refresh report is unreadable: {exc}"}


def _refresh_age_minutes(report: dict[str, Any], now: datetime) -> float | None:
    generated_at = report.get("generated_at") if isinstance(report, dict) else None
    if not generated_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (_ensure_aware(now) - _ensure_aware(parsed)).total_seconds() / 60)


def _teams_label(match: Match) -> str:
    team_a = match.team_a.name if match.team_a else str(match.team_a_id)
    team_b = match.team_b.name if match.team_b else str(match.team_b_id)
    return f"{team_a} vs {team_b}"


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str | None:
    return _ensure_aware(value).isoformat() if value is not None else None


def _write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check forecast scheduler gaps and settlement health.")
    parser.add_argument("--hours-ahead", type=int, default=168)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        report = build_forecast_gap_report(db, hours_ahead=args.hours_ahead)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
