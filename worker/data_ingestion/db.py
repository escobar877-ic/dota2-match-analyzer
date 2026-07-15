from __future__ import annotations

import sys
import os
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
    elif current_url is None:
        os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.database import SessionLocal
from app.db.models import Match, Player, Team
from app.tier_filter.cleanup_service import (
    MATCH_MISSING_TOURNAMENT_REASON,
    MATCH_TEAM_A_EXCLUDED_REASON,
    MATCH_TEAM_B_EXCLUDED_REASON,
    MATCH_TOURNAMENT_EXCLUDED_REASON,
    TEAM_EXCLUDED_REASON,
)
from app.patches.patch_service import upsert_match_patch_context
from app.tier_filter.tier1_matcher import Tier1Matcher
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.normalizer import NormalizedMatch, NormalizedPlayer, NormalizedTeam
from worker.data_ingestion.pro_match_quality import validate_verified_pro_match


def get_session() -> Session:
    return SessionLocal()


def upsert_team(db: Session, team: NormalizedTeam, matcher: Tier1Matcher | None = None) -> tuple[Team, bool]:
    matcher = matcher or Tier1Matcher()
    is_tier1 = matcher.is_tier1_team(team.name)
    existing = db.scalar(
        select(Team).where(
            Team.external_source == team.external_source,
            Team.external_id == team.external_id,
        )
    )
    if existing:
        existing.name = team.name or existing.name
        existing.logo_url = team.logo_url or existing.logo_url
        existing.country = team.country or existing.country
        existing.region = team.region or existing.region
        _mark_team_tier(existing, is_tier1)
        return existing, False

    created = Team(
        external_source=team.external_source,
        external_id=team.external_id,
        name=team.name,
        logo_url=team.logo_url,
        country=team.country,
        region=team.region,
    )
    _mark_team_tier(created, is_tier1)
    db.add(created)
    db.flush()
    return created, True


def upsert_player(db: Session, player: NormalizedPlayer) -> tuple[Player, bool]:
    existing = db.scalar(
        select(Player).where(
            Player.external_source == player.external_source,
            Player.external_id == player.external_id,
        )
    )
    team_id = None
    if player.team_external_id:
        team = find_team(db, player.external_source, player.team_external_id)
        team_id = team.id if team else None

    if existing:
        existing.nickname = player.nickname or existing.nickname
        existing.real_name = player.real_name or existing.real_name
        existing.team_id = team_id or existing.team_id
        existing.role = player.role or existing.role
        existing.country = player.country or existing.country
        return existing, False

    created = Player(
        external_source=player.external_source,
        external_id=player.external_id,
        nickname=player.nickname,
        real_name=player.real_name,
        team_id=team_id,
        role=player.role,
        country=player.country,
    )
    db.add(created)
    db.flush()
    return created, True


def upsert_match(
    db: Session,
    match: NormalizedMatch,
    matcher: Tier1Matcher | None = None,
    *,
    quality_scope: str = "tier1",
    enforce_tier1: bool = True,
) -> tuple[Match | None, bool]:
    matcher = matcher or Tier1Matcher()
    existing = _find_existing_match(db, match)

    team_a = ensure_team_from_match(
        db,
        source=match.external_source,
        external_id=match.team_a_external_id,
        fallback_name=match.team_a_name,
        matcher=matcher,
    )
    team_b = ensure_team_from_match(
        db,
        source=match.external_source,
        external_id=match.team_b_external_id,
        fallback_name=match.team_b_name,
        matcher=matcher,
    )
    if not team_a or not team_b:
        return None, False

    winner_team_id = None
    if match.winner_team_external_id:
        winner = find_team(db, match.external_source, match.winner_team_external_id)
        winner_team_id = winner.id if winner else None

    is_tier1_match, excluded_reason = _classify_match(match, team_a, team_b, matcher)
    if quality_scope == "verified_pro":
        pro_quality = validate_verified_pro_match(match)
        is_tier1_match = pro_quality.valid
        excluded_reason = ",".join(pro_quality.reasons) if pro_quality.reasons else None
        quality_reasons = pro_quality.reasons
    else:
        quality = validate_match(
            match,
            team_a_is_tier1=team_a.is_active_tier1,
            team_b_is_tier1=team_b.is_active_tier1,
            matcher=matcher,
        )
        quality_reasons = quality.reasons
    if quality_reasons:
        excluded_reason = ",".join(dict.fromkeys([*(excluded_reason or "").split(","), *quality_reasons]).keys())
        excluded_reason = excluded_reason.strip(",")

    if existing:
        existing.team_a_id = team_a.id
        existing.team_b_id = team_b.id
        existing.tournament_name = match.tournament_name or existing.tournament_name
        existing.tournament_tier = match.tournament_tier or existing.tournament_tier
        existing.start_time = match.start_time or existing.start_time
        existing.format = match.format or existing.format
        existing.status = match.status or existing.status
        existing.is_draw = match.is_draw
        if match.is_draw:
            existing.winner_team_id = None
        elif winner_team_id is not None:
            existing.winner_team_id = winner_team_id
        existing.is_tier1_match = is_tier1_match
        existing.excluded_reason = excluded_reason
        _safe_patch_context(db, existing)
        return existing, False

    if enforce_tier1 and not is_tier1_match:
        return None, False

    created = Match(
        external_source=match.external_source,
        external_id=match.external_id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        tournament_name=match.tournament_name,
        tournament_tier=match.tournament_tier,
        start_time=match.start_time,
        format=match.format,
        status=match.status,
        winner_team_id=winner_team_id,
        is_draw=match.is_draw,
        is_tier1_match=is_tier1_match,
        excluded_reason=excluded_reason,
    )
    db.add(created)
    db.flush()
    _safe_patch_context(db, created)
    return created, True


def find_team(db: Session, source: str, external_id: str) -> Team | None:
    return db.scalar(
        select(Team).where(
            Team.external_source == source,
            Team.external_id == external_id,
        )
    )


def _find_existing_match(db: Session, match: NormalizedMatch) -> Match | None:
    if match.external_id:
        existing = db.scalar(
            select(Match).where(
                Match.external_source == match.external_source,
                Match.external_id == match.external_id,
            )
        )
        if existing:
            return existing
    if not match.start_time or not match.tournament_name or not match.team_a_external_id or not match.team_b_external_id:
        return None
    team_a = aliased(Team)
    team_b = aliased(Team)
    return db.scalar(
        select(Match)
        .join(team_a, Match.team_a_id == team_a.id)
        .join(team_b, Match.team_b_id == team_b.id)
        .where(
            Match.external_source == match.external_source,
            team_a.external_source == match.external_source,
            team_a.external_id == match.team_a_external_id,
            team_b.external_source == match.external_source,
            team_b.external_id == match.team_b_external_id,
            Match.tournament_name == match.tournament_name,
            Match.start_time == match.start_time,
        )
        .limit(1)
    )


def ensure_team_from_match(
    db: Session,
    source: str,
    external_id: str,
    fallback_name: str | None,
    matcher: Tier1Matcher | None = None,
) -> Team | None:
    matcher = matcher or Tier1Matcher()
    team = find_team(db, source, external_id)
    if team:
        if fallback_name and team.name.startswith("Unknown Team"):
            team.name = fallback_name
        _mark_team_tier(team, matcher.is_tier1_team(team.name))
        return team

    created = Team(
        external_source=source,
        external_id=external_id,
        name=fallback_name or f"Unknown Team {external_id}",
    )
    _mark_team_tier(created, matcher.is_tier1_team(created.name))
    db.add(created)
    db.flush()
    return created


def _mark_team_tier(team: Team, is_tier1: bool) -> None:
    if is_tier1:
        team.tier = "tier1"
        team.is_active_tier1 = True
        team.excluded_reason = None
    else:
        team.tier = None
        team.is_active_tier1 = False
        team.excluded_reason = TEAM_EXCLUDED_REASON


def _classify_match(match: NormalizedMatch, team_a: Team, team_b: Team, matcher: Tier1Matcher) -> tuple[bool, str | None]:
    reasons = []
    if not team_a.is_active_tier1:
        reasons.append(MATCH_TEAM_A_EXCLUDED_REASON)
    if not team_b.is_active_tier1:
        reasons.append(MATCH_TEAM_B_EXCLUDED_REASON)
    if not match.tournament_name:
        reasons.append(MATCH_MISSING_TOURNAMENT_REASON)
    elif not matcher.is_tier1_tournament(match.tournament_name):
        reasons.append(MATCH_TOURNAMENT_EXCLUDED_REASON)

    return len(reasons) == 0, ",".join(reasons) if reasons else None


def _safe_patch_context(db: Session, match: Match) -> None:
    try:
        upsert_match_patch_context(db, match)
    except Exception:
        return None
