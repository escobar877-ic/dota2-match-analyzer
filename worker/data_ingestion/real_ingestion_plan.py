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
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Match
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_coverage import build_data_coverage_report
from worker.data_ingestion.db import get_session
from worker.data_ingestion.source_capabilities import SOURCE_CAPABILITIES, get_source_capabilities
from worker.data_ingestion.source_status import SOURCE_KEYS, get_source_statuses


REAL_INGESTION_PLAN_PATH = Path(ML_ARTIFACT_DIR) / "real_ingestion_plan.json"


def build_real_ingestion_plan(
    db: Session,
    *,
    artifact_path: str | Path | None = REAL_INGESTION_PLAN_PATH,
) -> dict[str, Any]:
    statuses = get_source_statuses(db)
    capabilities = get_source_capabilities()
    coverage = build_data_coverage_report(db, artifact_path=None)
    available_sources = [
        source
        for source, status in statuses.items()
        if status.enabled and (status.has_api_key or not SOURCE_CAPABILITIES[source].requires_api_key)
    ]
    if "csv_import" not in available_sources:
        available_sources.append("csv_import")
    missing_keys = [
        env_key
        for source, env_key in SOURCE_KEYS.items()
        if SOURCE_CAPABILITIES[source].requires_api_key and not os.getenv(env_key)
    ]
    real_historical = db.scalar(
        select(Match)
        .where(
            Match.is_tier1_match.is_(True),
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.external_source != "dev_seed",
        )
        .limit(1)
    )
    real_historical_count = db.scalar(
        select(Match.id)
        .where(
            Match.is_tier1_match.is_(True),
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.external_source != "dev_seed",
        )
        .count()
    ) if False else _real_historical_count(db)
    historical_count = coverage["tier1_historical_matches_count"]
    commands = [
        "bash scripts/check_data_sources.sh",
        "bash scripts/real_ingestion_plan.sh",
        "docker compose run --rm worker python -m worker.data_ingestion.import_quality_report imports/tier1_matches_template.csv",
        "docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_template.csv --dry-run",
        "docker compose run --rm worker python -m worker.data_ingestion.match_validation",
        "docker compose run --rm worker python -m worker.data_ingestion.data_coverage",
    ]
    blockers = []
    if missing_keys:
        blockers.append("STRATZ/PandaScore keys missing; detailed real coverage will be limited.")
    if real_historical_count < 300:
        blockers.append("Fewer than 300 real Tier 1 finished matches with winners.")
    if coverage.get("dev_seed_only"):
        blockers.append("Coverage report is dev_seed_only; import real Tier 1 matches before real training.")
    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "warning" if blockers else "ok",
        "available_sources": available_sources,
        "missing_keys": missing_keys,
        "source_status": {source: status.__dict__ for source, status in statuses.items()},
        "capabilities": capabilities,
        "coverage": {
            "tier1_historical_matches_count": historical_count,
            "real_tier1_historical_matches_count": real_historical_count,
            "dev_seed_only": coverage.get("dev_seed_only"),
            "training_readiness": coverage.get("training_readiness"),
            "usable_threshold_remaining": max(0, 300 - real_historical_count),
            "good_threshold_remaining": max(0, 1000 - real_historical_count),
        },
        "recommended_commands": commands,
        "blockers": blockers,
        "what_blocks_real_training": blockers,
        "safe_to_sync": True,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return plan


def _real_historical_count(db: Session) -> int:
    return len(
        db.scalars(
            select(Match.id).where(
                Match.is_tier1_match.is_(True),
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.external_source != "dev_seed",
            )
        ).all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build safe real Tier 1 ingestion plan.")
    parser.parse_args()
    db = get_session()
    try:
        print(json.dumps(build_real_ingestion_plan(db), indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
