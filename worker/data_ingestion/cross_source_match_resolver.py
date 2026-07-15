from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Match
from worker.data_ingestion.normalizer import normalize_lookup_key


SOURCE_PRIORITY = {
    "stratz": 5,
    "pandascore": 4,
    "opendota": 3,
    "csv_import": 2,
    "dev_seed": 1,
}


@dataclass(frozen=True)
class MergeDecision:
    updates: dict[str, Any]
    warnings: list[str]


def normalize_match_identity(match: Match) -> dict[str, Any]:
    team_ids = sorted([match.team_a_id, match.team_b_id])
    return {
        "team_ids": tuple(team_ids),
        "tournament": normalize_lookup_key(match.tournament_name or ""),
        "start_time": match.start_time,
    }


def find_possible_duplicate_matches(db: Session, match: Match, window_hours: int = 6) -> list[Match]:
    if match.start_time is None or match.tournament_name is None:
        return []
    lower = match.start_time - timedelta(hours=window_hours)
    upper = match.start_time + timedelta(hours=window_hours)
    candidates = db.scalars(
        select(Match).where(
            Match.id != match.id,
            Match.start_time >= lower,
            Match.start_time <= upper,
        )
    ).all()
    identity = normalize_match_identity(match)
    duplicates = []
    for candidate in candidates:
        candidate_identity = normalize_match_identity(candidate)
        if candidate_identity["team_ids"] != identity["team_ids"]:
            continue
        if candidate_identity["tournament"] != identity["tournament"]:
            continue
        duplicates.append(candidate)
    return duplicates


def choose_preferred_source(existing: Match, incoming: Match) -> str:
    existing_priority = SOURCE_PRIORITY.get(existing.external_source or "", 0)
    incoming_priority = SOURCE_PRIORITY.get(incoming.external_source or "", 0)
    return "incoming" if incoming_priority > existing_priority else "existing"


def merge_match_metadata_safely(existing: Match, incoming: Match) -> MergeDecision:
    warnings: list[str] = []
    updates: dict[str, Any] = {}
    preferred = choose_preferred_source(existing, incoming)

    if existing.winner_team_id and incoming.winner_team_id and existing.winner_team_id != incoming.winner_team_id:
        warnings.append("winner_conflict")
    elif not existing.winner_team_id and incoming.winner_team_id and preferred == "incoming":
        updates["winner_team_id"] = incoming.winner_team_id

    if existing.status != incoming.status and preferred == "incoming" and existing.status != "finished":
        updates["status"] = incoming.status
    elif existing.status != incoming.status:
        warnings.append("status_conflict")

    if not existing.format and incoming.format:
        updates["format"] = incoming.format
    elif existing.format and incoming.format and existing.format != incoming.format:
        warnings.append("format_conflict")

    if existing.team_a_id != incoming.team_a_id or existing.team_b_id != incoming.team_b_id:
        warnings.append("team_id_conflict")
    if normalize_lookup_key(existing.tournament_name or "") != normalize_lookup_key(incoming.tournament_name or ""):
        warnings.append("tournament_conflict")

    return MergeDecision(updates=updates, warnings=warnings)
