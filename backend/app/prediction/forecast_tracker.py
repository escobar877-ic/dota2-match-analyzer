from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, PredictionForecast
from app.prediction.prediction_service import build_match_prediction
from app.prediction.series_outcomes import calculate_series_outcomes
from app.prediction.verified_pro_preview import (
    build_verified_pro_preview,
    is_verified_pro_preview_match,
)
from ml.config import ML_ARTIFACT_DIR


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "prospective_accuracy_report.json"
HORIZON_ORDER = ("early", "day_before", "final")
MAX_EARLY_HOURS = 168.0
DAY_BEFORE_HOURS = 24.0
FINAL_HOURS = 2.0
SCHEDULE_DRIFT_MINUTES = 15.0


def snapshot_upcoming_forecasts(
    *,
    hours_ahead: int = 168,
    now: datetime | None = None,
    db_factory: Callable[[], Any] | None = None,
    strict_prediction_builder: Callable[[Any, Match], Any] | None = None,
    preview_prediction_builder: Callable[[Any, Match], Any] | None = None,
) -> dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    db = (db_factory or SessionLocal)()
    try:
        candidates = list(
            db.scalars(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(
                    Match.status == "upcoming",
                    Match.external_source == "pandascore",
                    Match.start_time > current_time,
                    Match.start_time <= current_time + timedelta(hours=max(1, hours_ahead)),
                )
                .order_by(Match.start_time, Match.id)
            ).all()
        )
        strict_matches = [match for match in candidates if _is_strict_forecast_match(match)]
        preview_matches = [
            match
            for match in candidates
            if not _is_strict_forecast_match(match)
            and is_verified_pro_preview_match(match, allow_finished=False)
        ]
        preview_match_ids = {match.id for match in preview_matches}
        matches = [*strict_matches, *preview_matches]
        existing_forecasts: dict[tuple[int, str], list[PredictionForecast]] = defaultdict(list)
        if matches:
            for forecast in db.scalars(
                select(PredictionForecast).where(
                    PredictionForecast.match_id.in_([match.id for match in matches])
                )
            ).all():
                existing_forecasts[(forecast.match_id, forecast.horizon_bucket)].append(forecast)
        created = 0
        rescheduled_snapshots = 0
        scope_upgrade_snapshots = 0
        skipped_existing = 0
        errors = []
        samples = []
        for match in matches:
            lead_time_hours = (
                _ensure_aware(match.start_time) - _ensure_aware(current_time)
            ).total_seconds() / 3600
            horizon_bucket = horizon_bucket_for_lead_time(lead_time_hours)
            if horizon_bucket is None:
                continue
            is_preview = match.id in preview_match_ids
            evaluation_scope = "verified_pro_preview" if is_preview else "strict_tier1"
            same_horizon = existing_forecasts.get((match.id, horizon_bucket), [])
            same_scope = [
                forecast
                for forecast in same_horizon
                if _forecast_evaluation_scope(forecast) == evaluation_scope
            ]
            if any(
                _schedule_difference_minutes(forecast.scheduled_start, match.start_time)
                <= SCHEDULE_DRIFT_MINUTES
                for forecast in same_scope
            ):
                skipped_existing += 1
                continue
            try:
                prediction = (
                    (preview_prediction_builder or build_verified_pro_preview)(db, match)
                    if is_preview
                    else (strict_prediction_builder or _build_complete_ensemble_prediction)(db, match)
                )
                outcomes = _prediction_outcomes(prediction)
                if horizon_bucket == "final":
                    for previous in same_scope:
                        previous.is_primary = False
                forecast = PredictionForecast(
                    match_id=match.id,
                    horizon_bucket=horizon_bucket,
                    is_primary=horizon_bucket == "final",
                    generated_at=current_time,
                    scheduled_start=match.start_time,
                    lead_time_hours=round(lead_time_hours, 2),
                    prediction_type=prediction.prediction_type,
                    evaluation_scope=evaluation_scope,
                    model_version=_effective_model_version(prediction),
                    team_a_probability=prediction.team_a_probability,
                    team_b_probability=prediction.team_b_probability,
                    confidence_label=prediction.confidence,
                    confidence_score=prediction.confidence_score,
                    predicted_outcomes_json=outcomes,
                    components_json={
                        key: value.model_dump()
                        for key, value in (prediction.components or {}).items()
                    }
                    or None,
                    guard_reasons_json=prediction.confidence_reasons or None,
                    status="pending",
                )
                db.add(forecast)
                db.flush()
                db.commit()
                created += 1
                if same_scope:
                    rescheduled_snapshots += 1
                elif same_horizon:
                    scope_upgrade_snapshots += 1
                existing_forecasts[(match.id, horizon_bucket)].append(forecast)
                samples.append(
                    {
                        "forecast_id": forecast.id,
                        "match_id": match.id,
                        "horizon_bucket": horizon_bucket,
                        "lead_time_hours": round(lead_time_hours, 2),
                        "start_time": match.start_time.isoformat(),
                        "outcomes": outcomes,
                        "confidence": prediction.confidence,
                        "evaluation_scope": "verified_pro_preview" if is_preview else "strict_tier1",
                        "schedule_revision": bool(same_horizon),
                    }
                )
            except Exception as exc:
                db.rollback()
                errors.append(f"match_id={match.id}: {exc.__class__.__name__}")
        return {
            "status": "warning" if errors else "ok",
            "created": created,
            "rescheduled_snapshots": rescheduled_snapshots,
            "scope_upgrade_snapshots": scope_upgrade_snapshots,
            "eligible_matches": len(matches),
            "strict_eligible_matches": len(strict_matches),
            "preview_eligible_matches": len(preview_matches),
            "skipped_existing": skipped_existing,
            "hours_ahead": hours_ahead,
            "errors": errors,
            "samples": samples,
        }
    finally:
        db.close()


def settle_forecasts(
    *,
    now: datetime | None = None,
    db_factory: Callable[[], Any] | None = None,
    report_writer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    db = (db_factory or SessionLocal)()
    try:
        forecasts = db.scalars(
            select(PredictionForecast)
            .options(selectinload(PredictionForecast.match))
            .where(PredictionForecast.status == "pending")
        ).all()
        settled = 0
        for forecast in forecasts:
            match = forecast.match
            if match.status != "finished":
                continue
            actual = _actual_outcome(match)
            if actual is None:
                continue
            metrics = score_outcome(forecast.predicted_outcomes_json, actual)
            forecast.status = "settled"
            forecast.actual_outcome = actual
            forecast.log_loss = metrics["log_loss"]
            forecast.brier_score = metrics["brier_score"]
            forecast.correct = metrics["correct"]
            forecast.settled_at = current_time
            settled += 1
        db.commit()
        report = build_prospective_report(db)
        (report_writer or _write_report)(report)
        return {"status": "ok", "settled_now": settled, "report": report}
    finally:
        db.close()


def score_outcome(probabilities: dict[str, float], actual: str) -> dict[str, Any]:
    keys = ["team_a", "draw", "team_b"] if "draw" in probabilities else ["team_a", "team_b"]
    normalized = _normalize_outcomes({key: float(probabilities.get(key, 0.0)) for key in keys})
    actual_probability = max(0.000001, normalized.get(actual, 0.0))
    brier = sum(
        (probability - (1.0 if outcome == actual else 0.0)) ** 2
        for outcome, probability in normalized.items()
    )
    predicted = max(normalized, key=normalized.get)
    return {
        "log_loss": round(-math.log(actual_probability), 6),
        "brier_score": round(brier, 6),
        "correct": predicted == actual,
    }


def horizon_bucket_for_lead_time(lead_time_hours: float) -> str | None:
    if lead_time_hours <= 0 or lead_time_hours > MAX_EARLY_HOURS:
        return None
    if lead_time_hours <= FINAL_HOURS:
        return "final"
    if lead_time_hours <= DAY_BEFORE_HOURS:
        return "day_before"
    return "early"


def build_prospective_report(db) -> dict[str, Any]:
    all_scope_forecasts = list(
        db.scalars(
            select(PredictionForecast)
            .options(selectinload(PredictionForecast.match))
            .order_by(PredictionForecast.generated_at)
        ).all()
    )
    preview_forecasts = [
        forecast
        for forecast in all_scope_forecasts
        if forecast.prediction_type == "verified_pro_preview"
    ]
    forecasts = [
        forecast
        for forecast in all_scope_forecasts
        if forecast.prediction_type != "verified_pro_preview"
    ]
    settled = [forecast for forecast in forecasts if forecast.status == "settled"]
    final_forecasts = [
        forecast
        for forecast in forecasts
        if forecast.horizon_bucket == "final" and forecast.is_primary
    ]
    final_settled = [forecast for forecast in final_forecasts if forecast.status == "settled"]
    by_confidence: dict[str, list[PredictionForecast]] = defaultdict(list)
    for forecast in final_settled:
        by_confidence[forecast.confidence_label].append(forecast)
    by_horizon = {}
    for horizon in HORIZON_ORDER:
        rows = [
            forecast
            for forecast in forecasts
            if forecast.horizon_bucket == horizon
            and (horizon != "final" or forecast.is_primary)
        ]
        settled_rows = [forecast for forecast in rows if forecast.status == "settled"]
        by_horizon[horizon] = {
            "total": len(rows),
            "pending": sum(forecast.status == "pending" for forecast in rows),
            "settled": len(settled_rows),
            "metrics": _aggregate_metrics(settled_rows),
            "component_metrics": _component_metrics(settled_rows),
            "by_format": _segment_metrics(settled_rows, _forecast_format),
        }
    tracked_settled_match_ids = {forecast.match_id for forecast in settled}
    final_settled_match_ids = {forecast.match_id for forecast in final_settled}
    final_capture_rate = (
        len(final_settled_match_ids) / len(tracked_settled_match_ids)
        if tracked_settled_match_ids
        else None
    )
    final_sample_ready = len(final_settled) >= 100
    return {
        "status": "ok" if len(final_settled) >= 100 else "warning",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_forecasts": len(forecasts),
        "all_scopes_total_forecasts": len(all_scope_forecasts),
        "total_matches": len({forecast.match_id for forecast in forecasts}),
        "pending_forecasts": sum(forecast.status == "pending" for forecast in forecasts),
        "settled_forecasts": len(settled),
        "superseded_final_forecasts": sum(
            forecast.horizon_bucket == "final" and not forecast.is_primary
            for forecast in forecasts
        ),
        "primary_horizon": "final",
        "primary_pending_forecasts": sum(
            forecast.status == "pending" for forecast in final_forecasts
        ),
        "primary_settled_forecasts": len(final_settled),
        "metrics": _aggregate_metrics(final_settled),
        "all_horizons_metrics": _aggregate_metrics(settled),
        "by_horizon": by_horizon,
        "by_confidence": {
            label: _aggregate_metrics(rows) for label, rows in sorted(by_confidence.items())
        },
        "by_format": _segment_metrics(final_settled, _forecast_format),
        "by_tournament": _segment_metrics(final_settled, _forecast_tournament),
        "by_prediction_type": _segment_metrics(final_settled, lambda row: row.prediction_type),
        "all_horizons_by_format": _segment_metrics(settled, _forecast_format),
        "all_horizons_by_tournament": _segment_metrics(settled, _forecast_tournament),
        "all_horizons_by_prediction_type": _segment_metrics(settled, lambda row: row.prediction_type),
        "component_metrics": _component_metrics(final_settled),
        "all_horizons_component_metrics": _component_metrics(settled),
        "coverage": {
            "tracked_settled_matches": len(tracked_settled_match_ids),
            "final_settled_matches": len(final_settled_match_ids),
            "final_capture_rate": round(final_capture_rate, 4) if final_capture_rate is not None else None,
            "minimum_final_forecasts": 100,
            "recommended_final_forecasts": 300,
        },
        "quality_gates": {
            "final_sample_size": "passed" if final_sample_ready else "collecting",
            "final_capture_rate": (
                "passed"
                if final_capture_rate is not None and final_capture_rate >= 0.95 and len(tracked_settled_match_ids) >= 20
                else "collecting"
            ),
            "betting_claims_allowed": False,
        },
        "verified_pro_preview": _build_preview_prospective_report(preview_forecasts),
        "warning": (
            None
            if len(final_settled) >= 100
            else "Fewer than 100 final prospective forecasts are settled; do not infer betting profitability."
        ),
    }


def _build_preview_prospective_report(
    forecasts: list[PredictionForecast],
) -> dict[str, Any]:
    settled = [forecast for forecast in forecasts if forecast.status == "settled"]
    final_forecasts = [
        forecast
        for forecast in forecasts
        if forecast.horizon_bucket == "final" and forecast.is_primary
    ]
    final_settled = [forecast for forecast in final_forecasts if forecast.status == "settled"]
    by_horizon: dict[str, Any] = {}
    for horizon in HORIZON_ORDER:
        rows = [
            forecast
            for forecast in forecasts
            if forecast.horizon_bucket == horizon
            and (horizon != "final" or forecast.is_primary)
        ]
        settled_rows = [forecast for forecast in rows if forecast.status == "settled"]
        by_horizon[horizon] = {
            "total": len(rows),
            "pending": sum(forecast.status == "pending" for forecast in rows),
            "settled": len(settled_rows),
            "metrics": _aggregate_metrics(settled_rows),
            "component_metrics": _component_metrics(settled_rows),
        }
    tracked_settled_match_ids = {forecast.match_id for forecast in settled}
    final_settled_match_ids = {forecast.match_id for forecast in final_settled}
    final_capture_rate = (
        len(final_settled_match_ids) / len(tracked_settled_match_ids)
        if tracked_settled_match_ids
        else None
    )
    return {
        "status": "ok" if len(final_settled) >= 100 else "collecting",
        "isolated_from_strict_metrics": True,
        "used_for_training": False,
        "used_for_promotion": False,
        "total_forecasts": len(forecasts),
        "pending_forecasts": sum(forecast.status == "pending" for forecast in forecasts),
        "settled_forecasts": len(settled),
        "primary_pending_forecasts": sum(
            forecast.status == "pending" for forecast in final_forecasts
        ),
        "primary_settled_forecasts": len(final_settled),
        "metrics": _aggregate_metrics(final_settled),
        "all_horizons_metrics": _aggregate_metrics(settled),
        "component_metrics": _component_metrics(final_settled),
        "all_horizons_component_metrics": _component_metrics(settled),
        "by_horizon": by_horizon,
        "by_format": _segment_metrics(final_settled, _forecast_format),
        "by_tournament": _segment_metrics(final_settled, _forecast_tournament),
        "all_horizons_by_format": _segment_metrics(settled, _forecast_format),
        "coverage": {
            "tracked_settled_matches": len(tracked_settled_match_ids),
            "final_settled_matches": len(final_settled_match_ids),
            "final_capture_rate": (
                round(final_capture_rate, 4) if final_capture_rate is not None else None
            ),
            "minimum_final_forecasts": 100,
        },
        "warning": (
            None
            if len(final_settled) >= 100
            else "Verified-pro preview metrics are preliminary and never replace strict Tier 1 metrics."
        ),
    }


def _aggregate_metrics(rows: list[PredictionForecast]) -> dict[str, float | int | None]:
    if not rows:
        return {
            "sample_size": 0,
            "accuracy": None,
            "log_loss": None,
            "brier_score": None,
        }
    return {
        "sample_size": len(rows),
        "accuracy": round(sum(bool(row.correct) for row in rows) / len(rows), 4),
        "log_loss": round(sum(float(row.log_loss) for row in rows) / len(rows), 6),
        "brier_score": round(sum(float(row.brier_score) for row in rows) / len(rows), 6),
    }


def _segment_metrics(rows: list[PredictionForecast], key_fn) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[PredictionForecast]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row) or "unknown")].append(row)
    return {
        key: _aggregate_metrics(grouped_rows)
        for key, grouped_rows in sorted(grouped.items())
    }


def _component_metrics(rows: list[PredictionForecast]) -> dict[str, dict[str, float | int | None]]:
    metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.actual_outcome:
            continue
        metrics["ensemble"].append(
            {
                "correct": bool(row.correct),
                "log_loss": float(row.log_loss),
                "brier_score": float(row.brier_score),
            }
        )
        for component_name, component in (row.components_json or {}).items():
            if not component.get("available") or component.get("team_a_probability") is None:
                continue
            map_probability = float(component["team_a_probability"])
            series = calculate_series_outcomes(map_probability, row.match.format if row.match else None)
            if series:
                probabilities = {
                    "team_a": float(series["team_a_win"]),
                    "team_b": float(series["team_b_win"]),
                }
                if float(series.get("draw") or 0.0) > 0:
                    probabilities["draw"] = float(series["draw"])
            else:
                probabilities = {"team_a": map_probability, "team_b": 1.0 - map_probability}
            metrics[component_name].append(score_outcome(probabilities, row.actual_outcome))
    return {
        name: _aggregate_scored_metrics(values)
        for name, values in sorted(metrics.items())
    }


def _aggregate_scored_metrics(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    if not rows:
        return {"sample_size": 0, "accuracy": None, "log_loss": None, "brier_score": None}
    return {
        "sample_size": len(rows),
        "accuracy": round(sum(bool(row["correct"]) for row in rows) / len(rows), 4),
        "log_loss": round(sum(float(row["log_loss"]) for row in rows) / len(rows), 6),
        "brier_score": round(sum(float(row["brier_score"]) for row in rows) / len(rows), 6),
    }


def _forecast_format(forecast: PredictionForecast) -> str:
    return (forecast.match.format if forecast.match else None) or "unknown"


def _forecast_tournament(forecast: PredictionForecast) -> str:
    return (forecast.match.tournament_name if forecast.match else None) or "unknown"


def _prediction_outcomes(prediction) -> dict[str, float]:
    if prediction.series_outcomes:
        outcomes = {
            "team_a": float(prediction.series_outcomes["team_a_win"]),
            "team_b": float(prediction.series_outcomes["team_b_win"]),
        }
        if float(prediction.series_outcomes.get("draw") or 0.0) > 0:
            outcomes["draw"] = float(prediction.series_outcomes["draw"])
        return _normalize_outcomes(outcomes)
    return _normalize_outcomes(
        {
            "team_a": prediction.team_a_probability,
            "team_b": prediction.team_b_probability,
        }
    )


def _normalize_outcomes(values: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in values.values())
    if total <= 0:
        return {key: round(1.0 / len(values), 6) for key in values}
    normalized = {key: round(max(0.0, value) / total, 6) for key, value in values.items()}
    last_key = list(normalized)[-1]
    normalized[last_key] = round(1.0 - sum(value for key, value in normalized.items() if key != last_key), 6)
    return normalized


def _effective_model_version(prediction) -> str:
    ml = (prediction.components or {}).get("ml")
    if ml and ml.model_version:
        return ml.model_version
    return prediction.model_version


def _forecast_evaluation_scope(forecast: PredictionForecast) -> str:
    return forecast.evaluation_scope or (
        "verified_pro_preview"
        if forecast.prediction_type == "verified_pro_preview"
        else "strict_tier1"
    )


def _schedule_difference_minutes(first: datetime, second: datetime) -> float:
    return abs((_ensure_aware(first) - _ensure_aware(second)).total_seconds()) / 60.0


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _build_complete_ensemble_prediction(db, match: Match, attempts: int = 3):
    last_reason = "full ensemble unavailable"
    for attempt in range(attempts):
        prediction = build_match_prediction(db, match)
        components = prediction.components or {}
        unavailable = [
            name
            for name in ("formula", "elo", "ml")
            if name not in components or not components[name].available
        ]
        if prediction.prediction_type == "ensemble" and not unavailable:
            return prediction
        last_reason = f"required components unavailable: {', '.join(unavailable) or prediction.prediction_type}"
        if attempt < attempts - 1:
            time.sleep(0.5)
    raise RuntimeError(last_reason)


def _is_strict_forecast_match(match: Match) -> bool:
    return bool(
        match.is_tier1_match
        and match.is_prediction_eligible
        and match.team_a
        and match.team_b
        and match.team_a.is_active_tier1
        and match.team_b.is_active_tier1
    )


def _actual_outcome(match: Match) -> str | None:
    if match.is_draw:
        return "draw"
    if match.winner_team_id == match.team_a_id:
        return "team_a"
    if match.winner_team_id == match.team_b_id:
        return "team_b"
    return None


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Track immutable prospective match predictions.")
    parser.add_argument("--snapshot-upcoming", action="store_true")
    parser.add_argument("--settle", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--hours-ahead", type=int, default=168)
    args = parser.parse_args()
    if args.snapshot_upcoming:
        print(json.dumps(snapshot_upcoming_forecasts(hours_ahead=args.hours_ahead), indent=2))
        return
    if args.settle:
        print(json.dumps(settle_forecasts(), indent=2))
        return
    db = SessionLocal()
    try:
        report = build_prospective_report(db)
        _write_report(report)
        print(json.dumps(report, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
