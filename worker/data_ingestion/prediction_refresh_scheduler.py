from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable


backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.betting.paper_bet_settlement import settle_paper_bets
from app.database import SessionLocal
from app.prediction.forecast_gap_report import (
    build_forecast_gap_report,
    write_prediction_refresh_report,
)
from app.prediction.prospective_decision import refresh_prospective_decision
from app.prediction.forecast_tracker import settle_forecasts, snapshot_upcoming_forecasts
from worker.data_ingestion.sync_tracked_results import sync_tracked_results
from worker.data_ingestion.sync_ewc_matches import sync_ewc_matches
from worker.data_ingestion.sync_live_match_context import sync_live_match_context
from worker.data_ingestion.sync_upcoming_matches import sync_upcoming_matches
from worker.data_ingestion.sync_upcoming_rosters import sync_upcoming_rosters


def run_prediction_refresh(
    *,
    hours_ahead: int = 168,
    schedule_limit: int = 100,
    operations: list[tuple[str, Callable[[], dict[str, Any]]]] | None = None,
    db_factory: Callable[[], Any] | None = None,
    health_builder: Callable[[Any], dict[str, Any]] | None = None,
    report_writer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    today = datetime.now(timezone.utc).date().isoformat()
    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = operations or [
        (
            "ewc_schedule",
            lambda: sync_ewc_matches(apply=True, limit=300),
        ),
        (
            "upcoming_schedule",
            lambda: sync_upcoming_matches(
                source="pandascore",
                from_date=today,
                limit=schedule_limit,
                dry_run=False,
            ),
        ),
        ("live_match_context", sync_live_match_context),
        ("upcoming_rosters", lambda: sync_upcoming_rosters(dry_run=False)),
        (
            "forecast_snapshots",
            lambda: snapshot_upcoming_forecasts(hours_ahead=hours_ahead),
        ),
        ("tracked_results", lambda: sync_tracked_results(dry_run=False)),
        ("settle_forecasts", settle_forecasts),
        ("prospective_decision", refresh_prospective_decision),
        ("settle_paper_bets", lambda: settle_paper_bets(dry_run=False)),
    ]
    results: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for name, operation in steps:
        try:
            with redirect_stdout(StringIO()):
                result = operation()
            results[name] = _summarize_step(result)
            step_status = str(result.get("status") or "ok").lower()
            step_errors = list(result.get("errors") or result.get("source_errors") or [])
            step_warnings = list(result.get("warnings") or [])
            if step_status == "failed":
                detail = f": {step_errors[0]}" if step_errors else ""
                errors.append(f"{name}: step failed{detail}")
            elif step_status == "warning":
                detail = f": {(step_errors or step_warnings)[0]}" if (step_errors or step_warnings) else ""
                warnings.append(f"{name}: step warning{detail}")
            warnings.extend(f"{name}: {warning}" for warning in step_warnings[:10])
        except Exception as exc:
            errors.append(f"{name}: {exc.__class__.__name__}: {exc}")
    cycle_status = "failed" if errors else "warning" if warnings else "ok"
    report: dict[str, Any] = {
        "status": cycle_status,
        "cycle_status": cycle_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round((datetime.now(timezone.utc) - started_at).total_seconds(), 2),
        "training_changed": False,
        "promotion_changed": False,
        "errors": errors,
        "warnings": warnings,
        "steps": results,
    }
    writer = report_writer or write_prediction_refresh_report
    try:
        writer(report)
    except Exception as exc:
        report["status"] = "warning"
        report["errors"] = [*errors, f"write_refresh_report: {exc.__class__.__name__}: {exc}"]
        return report

    db = (db_factory or SessionLocal)()
    try:
        health = (health_builder or build_forecast_gap_report)(db)
        report["forecast_health"] = {
            "status": health["status"],
            "summary": health["summary"],
            "checks": health["checks"],
            "warnings": health["warnings"][:10],
            "errors": health["errors"][:10],
        }
        if health["status"] != "ok" and report["status"] == "ok":
            report["status"] = "warning"
    except Exception as exc:
        report["status"] = "warning"
        report["errors"] = [
            *report["errors"],
            f"forecast_health: {exc.__class__.__name__}: {exc}",
        ]
    finally:
        db.close()
    try:
        writer(report)
    except Exception as exc:
        report["status"] = "failed"
        report["cycle_status"] = "failed"
        report["errors"] = [
            *report["errors"],
            f"write_final_refresh_report: {exc.__class__.__name__}: {exc}",
        ]
    return report


def _summarize_step(result: dict[str, Any]) -> dict[str, Any]:
    summary_keys = (
        "status",
        "mode",
        "records_seen",
        "records_created",
        "records_updated",
        "records_excluded",
        "prediction_eligible_count",
        "preserved_started_matches",
        "matched_live_matches",
        "drafts_available",
        "competition_counts",
        "status_counts",
        "teams_seen",
        "complete_rosters",
        "created",
        "rescheduled_snapshots",
        "scope_upgrade_snapshots",
        "eligible_matches",
        "strict_eligible_matches",
        "preview_eligible_matches",
        "skipped_existing",
        "tracked_matches",
        "finished_seen",
        "settled_now",
        "voided_now",
        "skipped_unfinished",
        "pending_seen",
        "decision_status",
        "strict_final_forecasts",
        "remaining_to_minimum",
        "recommended_action",
        "candidate_training_allowed",
        "promotion_allowed",
    )
    summary = {key: result[key] for key in summary_keys if key in result}
    errors = result.get("errors") or result.get("source_errors") or []
    warnings = result.get("warnings") or []
    if errors:
        summary["errors"] = list(errors)[:10]
    if warnings:
        summary["warnings"] = list(warnings)[:10]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh upcoming matches and immutable forecast horizons."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.getenv("FORECAST_REFRESH_INTERVAL_SECONDS", "900")),
    )
    parser.add_argument("--hours-ahead", type=int, default=168)
    parser.add_argument("--schedule-limit", type=int, default=100)
    args = parser.parse_args()

    while True:
        report = run_prediction_refresh(
            hours_ahead=args.hours_ahead,
            schedule_limit=args.schedule_limit,
        )
        print(json.dumps(report, indent=2, sort_keys=True, default=str), flush=True)
        if args.once:
            return
        time.sleep(max(300, args.interval_seconds))


if __name__ == "__main__":
    main()
