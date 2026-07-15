from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db.models import Hero, Match, MatchDraft, MatchPatchContext
from app.drafts.draft_service import get_draft_completeness, get_team_bans, get_team_picks


DRAFT_SAFE_DEFAULTS = {
    "draft_available": False,
    "draft_complete": False,
    "team_a_pick_count": 0,
    "team_b_pick_count": 0,
    "team_a_ban_count": 0,
    "team_b_ban_count": 0,
    "team_a_hero_pool_comfort": None,
    "team_b_hero_pool_comfort": None,
    "hero_pool_comfort_diff": None,
    "team_a_patch_hero_winrate": None,
    "team_b_patch_hero_winrate": None,
    "patch_hero_winrate_diff": None,
    "team_a_draft_synergy_score": None,
    "team_b_draft_synergy_score": None,
    "draft_synergy_diff": None,
}


def build_draft_features(db: Session, match: Match) -> dict:
    features = dict(DRAFT_SAFE_DEFAULTS)
    completeness = get_draft_completeness(db, match.id)
    features.update(
        {
            "draft_available": completeness["draft_available"],
            "draft_complete": completeness["draft_complete"],
            "team_a_pick_count": completeness["team_a_picks_count"],
            "team_b_pick_count": completeness["team_b_picks_count"],
            "team_a_ban_count": completeness["team_a_bans_count"],
            "team_b_ban_count": completeness["team_b_bans_count"],
        }
    )
    if not completeness["draft_available"]:
        return features

    team_a_picks = get_team_picks(db, match.id, match.team_a_id)
    team_b_picks = get_team_picks(db, match.id, match.team_b_id)
    team_a_hero_ids = [entry.hero_id for entry in team_a_picks]
    team_b_hero_ids = [entry.hero_id for entry in team_b_picks]
    a_comfort = _hero_pool_comfort(db, match.team_a_id, team_a_hero_ids, match)
    b_comfort = _hero_pool_comfort(db, match.team_b_id, team_b_hero_ids, match)
    a_patch = _patch_hero_winrate(db, match.team_a_id, team_a_hero_ids, match)
    b_patch = _patch_hero_winrate(db, match.team_b_id, team_b_hero_ids, match)
    a_synergy = _draft_synergy_score(db, team_a_hero_ids)
    b_synergy = _draft_synergy_score(db, team_b_hero_ids)
    features.update(
        {
            "team_a_hero_pool_comfort": a_comfort,
            "team_b_hero_pool_comfort": b_comfort,
            "hero_pool_comfort_diff": _diff(a_comfort, b_comfort),
            "team_a_patch_hero_winrate": a_patch,
            "team_b_patch_hero_winrate": b_patch,
            "patch_hero_winrate_diff": _diff(a_patch, b_patch),
            "team_a_draft_synergy_score": a_synergy,
            "team_b_draft_synergy_score": b_synergy,
            "draft_synergy_diff": _diff(a_synergy, b_synergy),
        }
    )
    return features


def _historical_drafts_for_team(db: Session, team_id: int, match: Match, hero_ids: list[int] | None = None) -> list[MatchDraft]:
    if match.start_time is None:
        return []
    statement = (
        select(MatchDraft)
        .join(Match, MatchDraft.match_id == Match.id)
        .where(
            MatchDraft.team_id == team_id,
            MatchDraft.action_type == "pick",
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.start_time.is_not(None),
            Match.start_time < match.start_time,
            Match.is_tier1_match.is_(True),
        )
    )
    if hero_ids:
        statement = statement.where(MatchDraft.hero_id.in_(hero_ids))
    return list(db.scalars(statement).all())


def _hero_pool_comfort(db: Session, team_id: int, hero_ids: list[int], match: Match) -> float | None:
    if not hero_ids:
        return None
    historical = _historical_drafts_for_team(db, team_id, match, hero_ids)
    if not historical:
        return None
    counts = Counter(entry.hero_id for entry in historical)
    return round(sum(counts.get(hero_id, 0) for hero_id in hero_ids) / max(1, len(hero_ids) * 5), 4)


def _patch_hero_winrate(db: Session, team_id: int, hero_ids: list[int], match: Match) -> float | None:
    if not hero_ids:
        return None
    current_patch_id = db.scalar(select(MatchPatchContext.patch_id).where(MatchPatchContext.match_id == match.id))
    if current_patch_id is None:
        return None
    historical = list(
        db.execute(
            select(Match, MatchDraft)
            .join(MatchDraft, MatchDraft.match_id == Match.id)
            .join(MatchPatchContext, MatchPatchContext.match_id == Match.id)
            .where(
                MatchDraft.team_id == team_id,
                MatchDraft.hero_id.in_(hero_ids),
                MatchDraft.action_type == "pick",
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                Match.start_time < match.start_time,
                Match.is_tier1_match.is_(True),
                MatchPatchContext.patch_id == current_patch_id,
            )
        ).all()
    )
    if not historical:
        return None
    wins = sum(1 for historical_match, _draft in historical if historical_match.winner_team_id == team_id)
    return round(wins / len(historical), 4)


def _draft_synergy_score(db: Session, hero_ids: list[int]) -> float | None:
    if len(hero_ids) < 2:
        return None
    heroes = list(db.scalars(select(Hero).where(Hero.id.in_(hero_ids))).all())
    role_counts = Counter(role for hero in heroes for role in (hero.roles_json or []))
    if not role_counts:
        return None
    support = role_counts.get("Support", 0)
    carry = role_counts.get("Carry", 0)
    disabler = role_counts.get("Disabler", 0)
    return round(min(1.0, (support * 0.25 + carry * 0.25 + disabler * 0.15 + len(role_counts) * 0.05)), 4)


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build experimental draft features for a match.")
    parser.add_argument("--match-id", type=int, required=True)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        match = db.get(Match, args.match_id)
        if match is None:
            raise SystemExit(f"Match {args.match_id} not found.")
        print(json.dumps(build_draft_features(db, match), indent=2, sort_keys=True, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
