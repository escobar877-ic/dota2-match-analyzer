from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Team
from app.tier_filter.tier1_config_loader import load_tier1_config


SUBTEAM_TOKENS = {"academy", "junior", "youth"}
SYNTHETIC_TEAM_SOURCES = {"dev_seed", "demo"}


def normalize_team_identity_name(value: str) -> str:
    normalized = value.casefold().replace("&", " and ")
    normalized = re.sub(r"[^\w]+", " ", normalized)
    return " ".join(normalized.split())


def canonical_team_identity_name(value: str) -> str:
    normalized = normalize_team_identity_name(value)
    canonical = _configured_aliases().get(normalized, normalized)
    if _contains_subteam_token(normalized) and not _contains_subteam_token(canonical):
        return normalized
    return canonical


def build_team_identity_index(db: Session) -> tuple[dict[int, str], dict[str, set[int]]]:
    id_to_identity: dict[int, str] = {}
    identity_to_ids: dict[str, set[int]] = {}
    for team_id, name in db.execute(select(Team.id, Team.name)):
        identity = canonical_team_identity_name(name)
        id_to_identity[team_id] = identity
        identity_to_ids.setdefault(identity, set()).add(team_id)
    return id_to_identity, identity_to_ids


def resolve_team_identity_ids(db: Session, team_id: int) -> set[int]:
    id_to_identity, identity_to_ids = build_team_identity_index(db)
    identity = id_to_identity.get(team_id)
    if identity is None:
        return {team_id}
    return identity_to_ids.get(identity, {team_id})


def resolve_scoped_team_identity_ids(db: Session, team_id: int) -> set[int]:
    """Resolve exact canonical identities without mixing synthetic and real sources."""
    team = db.get(Team, team_id)
    if team is None:
        return {team_id}
    synthetic = team.external_source in SYNTHETIC_TEAM_SOURCES
    identity_ids = resolve_team_identity_ids(db, team_id)
    scoped_ids = {
        candidate_id
        for candidate_id, source in db.execute(
            select(Team.id, Team.external_source).where(Team.id.in_(identity_ids))
        )
        if (source in SYNTHETIC_TEAM_SOURCES) == synthetic
    }
    return scoped_ids or {team_id}


@lru_cache(maxsize=1)
def _configured_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    config = load_tier1_config()
    for team in config.teams:
        if not team.active:
            continue
        canonical = normalize_team_identity_name(team.name)
        aliases[canonical] = canonical
        for alias in team.aliases:
            normalized_alias = normalize_team_identity_name(alias)
            if _contains_subteam_token(normalized_alias) and not _contains_subteam_token(canonical):
                continue
            aliases[normalized_alias] = canonical
    return aliases


def _contains_subteam_token(value: str) -> bool:
    return bool(set(value.split()) & SUBTEAM_TOKENS)
