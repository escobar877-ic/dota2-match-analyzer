from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, PredictionForecast
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "tracked_result_sync_report.json"


def sync_tracked_results(*, dry_run: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    client = PandaScoreClient()
    started_at = datetime.now(timezone.utc)
    counters = SyncCounters()
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    finished_seen = 0
    try:
        matches = db.scalars(
            select(Match)
            .join(PredictionForecast, PredictionForecast.match_id == Match.id)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(
                PredictionForecast.status == "pending",
                Match.external_source == "pandascore",
            )
            .order_by(Match.start_time, Match.id)
        ).all()
        counters.records_seen = len(matches)
        for match in matches:
            if not match.external_id:
                errors.append(f"match_id={match.id}: missing PandaScore external_id")
                continue
            response = client.get_match(match.external_id)
            if not response.ok or not isinstance(response.data, dict):
                errors.append(f"match_id={match.id}: {response.error or 'invalid response'}")
                continue
            result = classify_pandascore_result(match, response.data)
            if result["status"] != "finished":
                continue
            finished_seen += 1
            if result["error"]:
                errors.append(f"match_id={match.id}: {result['error']}")
                continue
            if len(samples) < 20:
                samples.append(
                    {
                        "match_id": match.id,
                        "external_id": match.external_id,
                        "is_draw": result["is_draw"],
                        "winner_team_id": result["winner_team_id"],
                    }
                )
            if dry_run:
                continue
            match.status = "finished"
            match.is_draw = result["is_draw"]
            match.winner_team_id = result["winner_team_id"]
            match.is_training_eligible = False
            counters.records_updated += 1

        if dry_run:
            db.rollback()
        else:
            write_sync_log(
                db,
                source="pandascore",
                sync_type="tracked_results",
                status="warning" if errors else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(errors) if errors else None,
                metadata_json={"finished_seen": finished_seen},
            )
            db.commit()
        report = {
            "status": "warning" if errors else "ok",
            "mode": "dry_run" if dry_run else "apply",
            "tracked_matches": len(matches),
            "finished_seen": finished_seen,
            "would_update": finished_seen if dry_run else 0,
            "records_updated": counters.records_updated,
            "errors": errors,
            "samples": samples,
            "training_changed": False,
        }
        _write_report(report)
        return report
    finally:
        db.close()


def classify_pandascore_result(match: Match, raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "").lower()
    if status != "finished":
        return {
            "status": status,
            "is_draw": False,
            "winner_team_id": None,
            "error": None,
        }
    if bool(raw.get("draw")):
        return {
            "status": "finished",
            "is_draw": True,
            "winner_team_id": None,
            "error": None,
        }
    winner_external_id = str(
        raw.get("winner_id")
        or ((raw.get("winner") or {}).get("id") if isinstance(raw.get("winner"), dict) else "")
        or ""
    )
    if winner_external_id and match.team_a.external_id == winner_external_id:
        winner_team_id = match.team_a_id
    elif winner_external_id and match.team_b.external_id == winner_external_id:
        winner_team_id = match.team_b_id
    else:
        return {
            "status": "finished",
            "is_draw": False,
            "winner_team_id": None,
            "error": "finished result winner does not match either team",
        }
    return {
        "status": "finished",
        "is_draw": False,
        "winner_team_id": winner_team_id,
        "error": None,
    }


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh results for prospectively tracked matches.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync_tracked_results(dry_run=not args.apply), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
