from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DraftSnapshot, Match, MatchDraft


def get_match_draft(db: Session, match_id: int) -> list[MatchDraft]:
    return list(
        db.scalars(
            select(MatchDraft)
            .options(selectinload(MatchDraft.hero), selectinload(MatchDraft.team), selectinload(MatchDraft.player))
            .where(MatchDraft.match_id == match_id)
            .order_by(MatchDraft.draft_order.asc(), MatchDraft.id.asc())
        ).all()
    )


def get_draft_completeness(db: Session, match_id: int) -> dict:
    match = db.get(Match, match_id)
    if match is None:
        return _empty_completeness(match_id)
    entries = get_match_draft(db, match_id)
    team_a_picks = _count(entries, match.team_a_id, "pick")
    team_b_picks = _count(entries, match.team_b_id, "pick")
    team_a_bans = _count(entries, match.team_a_id, "ban")
    team_b_bans = _count(entries, match.team_b_id, "ban")
    return {
        "match_id": match_id,
        "draft_available": bool(entries),
        "draft_complete": team_a_picks >= 5 and team_b_picks >= 5,
        "team_a_picks_count": team_a_picks,
        "team_b_picks_count": team_b_picks,
        "team_a_bans_count": team_a_bans,
        "team_b_bans_count": team_b_bans,
    }


def validate_draft(db: Session, match_id: int) -> dict:
    completeness = get_draft_completeness(db, match_id)
    errors = []
    if completeness["team_a_picks_count"] > 5:
        errors.append("team_a_too_many_picks")
    if completeness["team_b_picks_count"] > 5:
        errors.append("team_b_too_many_picks")
    return {"valid": not errors, "errors": errors, **completeness}


def get_team_picks(db: Session, match_id: int, team_id: int) -> list[MatchDraft]:
    return [entry for entry in get_match_draft(db, match_id) if entry.team_id == team_id and entry.action_type == "pick"]


def get_team_bans(db: Session, match_id: int, team_id: int) -> list[MatchDraft]:
    return [entry for entry in get_match_draft(db, match_id) if entry.team_id == team_id and entry.action_type == "ban"]


def build_draft_snapshot(db: Session, match_id: int, source: str | None = None) -> DraftSnapshot | None:
    match = db.get(Match, match_id)
    if match is None:
        return None
    completeness = get_draft_completeness(db, match_id)
    snapshot = DraftSnapshot(
        match_id=match_id,
        draft_complete=completeness["draft_complete"],
        team_a_picks_count=completeness["team_a_picks_count"],
        team_b_picks_count=completeness["team_b_picks_count"],
        team_a_bans_count=completeness["team_a_bans_count"],
        team_b_bans_count=completeness["team_b_bans_count"],
        source=source,
    )
    db.add(snapshot)
    return snapshot


def draft_to_dict(db: Session, match: Match) -> dict:
    entries = get_match_draft(db, match.id)
    completeness = get_draft_completeness(db, match.id)
    return {
        **completeness,
        "entries": [_entry_to_dict(entry) for entry in entries],
    }


def _count(entries: list[MatchDraft], team_id: int, action_type: str) -> int:
    return sum(1 for entry in entries if entry.team_id == team_id and entry.action_type == action_type)


def _empty_completeness(match_id: int) -> dict:
    return {
        "match_id": match_id,
        "draft_available": False,
        "draft_complete": False,
        "team_a_picks_count": 0,
        "team_b_picks_count": 0,
        "team_a_bans_count": 0,
        "team_b_bans_count": 0,
    }


def _entry_to_dict(entry: MatchDraft) -> dict:
    return {
        "id": entry.id,
        "match_id": entry.match_id,
        "team_id": entry.team_id,
        "hero_id": entry.hero_id,
        "hero": {
            "id": entry.hero.id,
            "hero_id": entry.hero.hero_id,
            "localized_name": entry.hero.localized_name,
            "name": entry.hero.name,
        }
        if entry.hero
        else None,
        "player_id": entry.player_id,
        "action_type": entry.action_type,
        "pick_order": entry.pick_order,
        "ban_order": entry.ban_order,
        "draft_order": entry.draft_order,
        "side": entry.side,
        "source": entry.source,
    }
