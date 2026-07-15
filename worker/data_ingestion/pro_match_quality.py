from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from worker.data_ingestion.normalizer import NormalizedMatch, normalize_lookup_key


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "verified_pro_tournaments.json"
TRUSTED_PRO_SOURCES = {"pandascore", "stratz", "csv_import"}
MAX_HISTORICAL_AGE = timedelta(days=900)
BAD_TEAM_TOKENS = {"tbd", "unknown", "bye", "forfeit", "walkover", "academy", "junior", "youth"}
BLOCKED_TOURNAMENT_TOKENS = {"qualifier", "qualification", "open qualifier", "closed qualifier"}


@dataclass(frozen=True)
class ProQualityResult:
    valid: bool
    quality_tier: str
    reasons: list[str]


def validate_verified_pro_match(match: NormalizedMatch, *, now: datetime | None = None) -> ProQualityResult:
    now = now or datetime.now(UTC)
    reasons: list[str] = []

    if match.external_source not in TRUSTED_PRO_SOURCES:
        reasons.append("source_not_trusted_for_verified_pro")
    if not match.team_a_external_id or not match.team_b_external_id:
        reasons.append("match_missing_team")
    if _bad_team_name(match.team_a_name):
        reasons.append("team_a_low_confidence_name")
    if _bad_team_name(match.team_b_name):
        reasons.append("team_b_low_confidence_name")
    if not match.tournament_name:
        reasons.append("missing_tournament_name")
    elif not is_verified_pro_tournament(match.tournament_name):
        reasons.append("tournament_not_verified_pro")
    elif any(token in normalize_lookup_key(match.tournament_name) for token in BLOCKED_TOURNAMENT_TOKENS):
        reasons.append("qualifier_not_training_eligible")
    if match.start_time is None:
        reasons.append("match_missing_start_time")
    else:
        start = _aware(match.start_time)
        if start < now - MAX_HISTORICAL_AGE:
            reasons.append("match_too_old_for_verified_pro")
        if start > now + timedelta(days=370):
            reasons.append("match_start_time_too_far_future")
    if match.status == "finished" and not match.winner_team_external_id and not match.is_draw:
        reasons.append("finished_match_missing_winner")

    return ProQualityResult(valid=not reasons, quality_tier="verified_pro" if not reasons else "rejected", reasons=reasons)


def is_verified_pro_tournament(name: str) -> bool:
    key = normalize_lookup_key(name)
    aliases = _verified_tournament_aliases()
    return key in aliases or any(key.startswith(alias) or alias.startswith(key) for alias in aliases)


def _verified_tournament_aliases() -> set[str]:
    try:
        records = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        records = []
    aliases: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("active", True):
            continue
        names = [record.get("name"), *(record.get("aliases") or [])]
        for value in names:
            if value:
                aliases.add(normalize_lookup_key(str(value)))
    return aliases


def _bad_team_name(name: str | None) -> bool:
    if not name or not name.strip():
        return True
    key = normalize_lookup_key(name)
    return any(token in key for token in BAD_TEAM_TOKENS)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
