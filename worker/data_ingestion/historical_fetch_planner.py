from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.tier_filter.tier1_config_loader import load_tier1_config
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_coverage import build_data_coverage_report
from worker.data_ingestion.db import get_session
from worker.data_ingestion.source_capabilities import SOURCE_CAPABILITIES, get_source_capabilities
from worker.data_ingestion.source_status import SOURCE_KEYS, get_source_statuses


HISTORICAL_FETCH_PLAN_PATH = Path(ML_ARTIFACT_DIR) / "historical_fetch_plan.json"


def build_historical_fetch_plan(
    *,
    artifact_path: str | Path | None = HISTORICAL_FETCH_PLAN_PATH,
    coverage: dict[str, Any] | None = None,
    statuses: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if coverage is None or statuses is None:
        db = get_session()
        try:
            coverage = coverage or build_data_coverage_report(db, artifact_path=None)
            statuses = statuses or get_source_statuses(db)
        finally:
            db.close()
    config = load_tier1_config()
    today = datetime.now(UTC).date()
    windows = [
        {"label": "last_30_days", "start_date": str(today - timedelta(days=30)), "end_date": str(today)},
        {"label": "last_90_days", "start_date": str(today - timedelta(days=90)), "end_date": str(today)},
        {"label": "current_season", "start_date": f"{today.year}-01-01", "end_date": str(today)},
    ]
    available_sources = [
        source
        for source, status in statuses.items()
        if status.enabled and (status.has_api_key or not SOURCE_CAPABILITIES[source].requires_api_key)
    ]
    blockers = []
    for source, env_key in SOURCE_KEYS.items():
        if SOURCE_CAPABILITIES[source].requires_api_key and not os.getenv(env_key):
            blockers.append(f"{env_key} missing; {source} historical fetch disabled.")
    if coverage.get("dev_seed_only"):
        blockers.append("Current coverage is dev_seed_only; first real sync should be reviewed carefully.")
    report = {
        "status": "warning" if blockers else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "tier1_team_count": len(config.teams),
        "tier1_tournament_count": len(config.tournaments),
        "available_sources": available_sources,
        "recommended_windows": windows,
        "coverage": {
            "tier1_historical_matches_count": coverage.get("tier1_historical_matches_count"),
            "training_readiness": coverage.get("training_readiness"),
            "dev_seed_only": coverage.get("dev_seed_only"),
        },
        "capabilities": get_source_capabilities(),
        "blockers": blockers,
        "command_hints": [
            "bash scripts/sync_historical_matches.sh --source opendota --start-date YYYY-MM-DD --end-date YYYY-MM-DD --limit 100",
            "Add --apply only after reviewing dry-run, validation, audit, and coverage.",
        ],
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan safe historical Tier 1 fetch windows.")
    parser.parse_args()
    print(json.dumps(build_historical_fetch_plan(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
