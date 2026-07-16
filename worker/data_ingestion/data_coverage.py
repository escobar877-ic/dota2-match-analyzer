from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DotaPatch, Match, MatchPatchContext, Team, TeamRoster
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session


COVERAGE_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "data_coverage_report.json"


def training_readiness(match_count: int) -> str:
    if match_count >= 1000:
        return "good"
    if match_count >= 300:
        return "usable"
    return "insufficient"


def build_data_coverage_report(
    db: Session,
    *,
    artifact_path: str | Path | None = COVERAGE_REPORT_PATH,
) -> dict[str, Any]:
    matches = db.scalars(
        select(Match).where(
            Match.is_tier1_match.is_(True),
            Match.status == "finished",
        )
    ).all()
    match_ids = [match.id for match in matches]
    with_winner = [match for match in matches if match.winner_team_id is not None]
    tournament_counts = Counter(match.tournament_name or "Unknown" for match in matches)
    source_counts = Counter(match.external_source or "unknown" for match in matches)
    patch_counts = _matches_by_patch(db, match_ids)
    patch_context_count = _patch_context_count(db, match_ids)
    roster_context_count = sum(1 for match in matches if _has_roster_context(db, match))
    start_times = [match.start_time for match in matches if match.start_time is not None]
    real_tier1_count = sum(1 for match in matches if match.external_source != "dev_seed")
    dev_seed_count = source_counts.get("dev_seed", 0)
    verified_pro_count = db.scalar(
        select(func.count())
        .select_from(Match)
        .where(
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.competition_tier == "pro",
            Match.verification_status == "verified",
            Match.is_training_eligible.is_(True),
            Match.external_source != "dev_seed",
        )
    ) or 0
    real_training_eligible_count = real_tier1_count + verified_pro_count
    readiness = training_readiness(real_training_eligible_count)
    dev_seed_only = bool(matches) and set(source_counts) == {"dev_seed"}

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tier1_teams_count": db.scalar(select(func.count()).select_from(Team).where(Team.is_active_tier1.is_(True))) or 0,
        "tier1_historical_matches_count": len(matches),
        "real_tier1_historical_matches_count": real_tier1_count,
        "verified_pro_historical_matches_count": verified_pro_count,
        "real_training_eligible_matches_count": real_training_eligible_count,
        "dev_seed_historical_matches_count": dev_seed_count,
        "matches_with_winner_count": len(with_winner),
        "matches_with_patch_context_count": patch_context_count,
        "matches_with_roster_context_count": roster_context_count,
        "patch_coverage_ratio": _ratio(patch_context_count, len(matches)),
        "roster_coverage_ratio": _ratio(roster_context_count, len(matches)),
        "matches_by_tournament": dict(sorted(tournament_counts.items())),
        "matches_by_patch": patch_counts,
        "matches_by_source": dict(sorted(source_counts.items())),
        "date_range": {
            "from": min(start_times).isoformat() if start_times else None,
            "to": max(start_times).isoformat() if start_times else None,
        },
        "training_readiness": readiness,
        "enough_for_training": readiness in {"usable", "good"},
        "dev_seed_only": dev_seed_only,
        "warning": "Coverage is synthetic dev seed only and is not real accuracy." if dev_seed_only else None,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp_path.replace(path)
    return report


def _patch_context_count(db: Session, match_ids: list[int]) -> int:
    if not match_ids:
        return 0
    return db.scalar(
        select(func.count())
        .select_from(MatchPatchContext)
        .where(MatchPatchContext.match_id.in_(match_ids))
    ) or 0


def _matches_by_patch(db: Session, match_ids: list[int]) -> dict[str, int]:
    if not match_ids:
        return {}
    rows = db.execute(
        select(DotaPatch.patch_version, func.count(MatchPatchContext.id))
        .join(MatchPatchContext, MatchPatchContext.patch_id == DotaPatch.id)
        .where(MatchPatchContext.match_id.in_(match_ids))
        .group_by(DotaPatch.patch_version)
        .order_by(DotaPatch.patch_version)
    ).all()
    return {patch_version: count for patch_version, count in rows}


def _has_roster_context(db: Session, match: Match) -> bool:
    if match.start_time is None:
        return False
    return _team_has_roster_context(db, match.team_a_id, match.start_time) and _team_has_roster_context(
        db, match.team_b_id, match.start_time
    )


def _team_has_roster_context(db: Session, team_id: int, at_time: datetime) -> bool:
    return (
        db.scalar(
            select(func.count(func.distinct(TeamRoster.player_id)))
            .where(
                TeamRoster.team_id == team_id,
                TeamRoster.start_date <= at_time,
                (TeamRoster.end_date.is_(None)) | (TeamRoster.end_date > at_time),
            )
        )
        == 5
    )


def _ratio(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report Tier 1 historical data coverage.")
    parser.parse_args()
    db = get_session()
    try:
        report = build_data_coverage_report(db)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
