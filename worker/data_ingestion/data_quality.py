from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Match
from app.tier_filter.cleanup_service import (
    MATCH_MISSING_TOURNAMENT_REASON,
    MATCH_TEAM_A_EXCLUDED_REASON,
    MATCH_TEAM_B_EXCLUDED_REASON,
    MATCH_TOURNAMENT_EXCLUDED_REASON,
    TEAM_EXCLUDED_REASON,
)
from app.tier_filter.tier1_matcher import Tier1Matcher
from worker.data_ingestion.normalizer import NormalizedMatch, NormalizedTeam, normalize_lookup_key


MATCH_MISSING_TEAM_REASON = "match_missing_team"
MATCH_MISSING_START_TIME_REASON = "match_missing_start_time"
MATCH_FINISHED_MISSING_WINNER_REASON = "finished_match_missing_winner"


@dataclass(frozen=True)
class QualityResult:
    valid: bool
    is_tier1: bool
    reasons: list[str]


def validate_team(team: NormalizedTeam, matcher: Tier1Matcher | None = None) -> QualityResult:
    matcher = matcher or Tier1Matcher()
    reasons = []
    if not team.name.strip():
        reasons.append("team_missing_name")
    is_tier1 = bool(team.name and matcher.is_tier1_team(team.name))
    if not is_tier1:
        reasons.append(TEAM_EXCLUDED_REASON)
    return QualityResult(valid=not reasons, is_tier1=is_tier1, reasons=reasons)


def validate_match(
    match: NormalizedMatch,
    *,
    team_a_is_tier1: bool | None = None,
    team_b_is_tier1: bool | None = None,
    matcher: Tier1Matcher | None = None,
) -> QualityResult:
    matcher = matcher or Tier1Matcher()
    reasons = []
    if not match.team_a_external_id or not match.team_b_external_id:
        reasons.append(MATCH_MISSING_TEAM_REASON)
    if team_a_is_tier1 is None and match.team_a_name:
        team_a_is_tier1 = matcher.is_tier1_team(match.team_a_name)
    if team_b_is_tier1 is None and match.team_b_name:
        team_b_is_tier1 = matcher.is_tier1_team(match.team_b_name)
    if team_a_is_tier1 is False:
        reasons.append(MATCH_TEAM_A_EXCLUDED_REASON)
    if team_b_is_tier1 is False:
        reasons.append(MATCH_TEAM_B_EXCLUDED_REASON)
    if not match.tournament_name:
        reasons.append(MATCH_MISSING_TOURNAMENT_REASON)
    elif not matcher.is_tier1_tournament(match.tournament_name):
        reasons.append(MATCH_TOURNAMENT_EXCLUDED_REASON)
    if match.start_time is None:
        reasons.append(MATCH_MISSING_START_TIME_REASON)
    if match.status == "finished" and not match.winner_team_external_id and not match.is_draw:
        reasons.append(MATCH_FINISHED_MISSING_WINNER_REASON)

    is_tier1 = (
        MATCH_TEAM_A_EXCLUDED_REASON not in reasons
        and MATCH_TEAM_B_EXCLUDED_REASON not in reasons
        and MATCH_TOURNAMENT_EXCLUDED_REASON not in reasons
        and MATCH_MISSING_TOURNAMENT_REASON not in reasons
    )
    return QualityResult(valid=not reasons, is_tier1=is_tier1, reasons=reasons)


def duplicate_key_for_match(match: NormalizedMatch) -> tuple:
    if match.external_id:
        return ("external", match.external_source, match.external_id)
    return (
        "tuple",
        match.external_source,
        normalize_lookup_key(match.team_a_name or match.team_a_external_id),
        normalize_lookup_key(match.team_b_name or match.team_b_external_id),
        normalize_lookup_key(match.tournament_name or ""),
        match.start_time.isoformat() if match.start_time else None,
    )


def matches_existing_tuple(match: NormalizedMatch, existing: Match) -> bool:
    if not existing.start_time or not match.start_time:
        return False
    return (
        normalize_lookup_key(existing.tournament_name or "") == normalize_lookup_key(match.tournament_name or "")
        and existing.start_time == match.start_time
    )
