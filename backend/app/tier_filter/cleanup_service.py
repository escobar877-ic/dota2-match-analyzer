from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")
    elif current_url is None:
        os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.db.models import Match, Team
from app.tier_filter.tier1_matcher import Tier1Matcher


TEAM_EXCLUDED_REASON = "team_not_in_tier1_allowlist"
MATCH_TEAM_A_EXCLUDED_REASON = "team_a_not_tier1"
MATCH_TEAM_B_EXCLUDED_REASON = "team_b_not_tier1"
MATCH_MISSING_TOURNAMENT_REASON = "missing_tournament_name"
MATCH_TOURNAMENT_EXCLUDED_REASON = "tournament_not_tier1_allowlist"


@dataclass(frozen=True)
class CleanupSummary:
    mode: str
    tier1_teams_count: int
    excluded_teams_count: int
    tier1_matches_count: int
    excluded_matches_count: int
    team_excluded_reasons: dict[str, int]
    match_excluded_reasons: dict[str, int]


def cleanup_tier1_data(
    db: Session,
    *,
    apply: bool = False,
    matcher: Tier1Matcher | None = None,
) -> CleanupSummary:
    matcher = matcher or Tier1Matcher()
    teams = list(db.scalars(select(Team).order_by(Team.id.asc())).all())
    team_is_tier1: dict[int, bool] = {}
    team_reasons: Counter[str] = Counter()

    for team in teams:
        is_tier1 = matcher.is_tier1_team(team.name)
        team_is_tier1[team.id] = is_tier1

        if is_tier1:
            if apply:
                team.tier = "tier1"
                team.is_active_tier1 = True
                team.excluded_reason = None
        else:
            team_reasons[TEAM_EXCLUDED_REASON] += 1
            if apply:
                team.tier = None
                team.is_active_tier1 = False
                team.excluded_reason = TEAM_EXCLUDED_REASON

    matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .order_by(Match.id.asc())
        ).all()
    )
    match_reasons: Counter[str] = Counter()
    tier1_matches_count = 0

    for match in matches:
        reasons = _match_excluded_reasons(match, team_is_tier1, matcher)
        is_tier1_match = len(reasons) == 0

        if is_tier1_match:
            tier1_matches_count += 1
            if apply:
                match.is_tier1_match = True
                match.excluded_reason = None
        else:
            match_reasons.update(reasons)
            if apply:
                match.is_tier1_match = False
                match.excluded_reason = ",".join(reasons)

    if apply:
        db.commit()

    tier1_teams_count = sum(1 for is_tier1 in team_is_tier1.values() if is_tier1)
    excluded_teams_count = len(team_is_tier1) - tier1_teams_count
    excluded_matches_count = len(matches) - tier1_matches_count

    return CleanupSummary(
        mode="apply" if apply else "dry-run",
        tier1_teams_count=tier1_teams_count,
        excluded_teams_count=excluded_teams_count,
        tier1_matches_count=tier1_matches_count,
        excluded_matches_count=excluded_matches_count,
        team_excluded_reasons=dict(sorted(team_reasons.items())),
        match_excluded_reasons=dict(sorted(match_reasons.items())),
    )


def _match_excluded_reasons(match: Match, team_is_tier1: dict[int, bool], matcher: Tier1Matcher) -> list[str]:
    reasons: list[str] = []
    if not team_is_tier1.get(match.team_a_id, False):
        reasons.append(MATCH_TEAM_A_EXCLUDED_REASON)
    if not team_is_tier1.get(match.team_b_id, False):
        reasons.append(MATCH_TEAM_B_EXCLUDED_REASON)

    if not match.tournament_name:
        reasons.append(MATCH_MISSING_TOURNAMENT_REASON)
    elif not matcher.is_tier1_tournament(match.tournament_name):
        reasons.append(MATCH_TOURNAMENT_EXCLUDED_REASON)

    return reasons


def print_summary(summary: CleanupSummary) -> None:
    print(json.dumps(asdict(summary), indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark Tier 1 and lower-tier local data without deleting rows.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Calculate cleanup counts without changing the database.")
    mode.add_argument("--apply", action="store_true", help="Persist Tier 1 and excluded markers without deleting data.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = cleanup_tier1_data(db, apply=args.apply)
        print_summary(summary)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
