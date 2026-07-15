from __future__ import annotations

from datetime import datetime
from typing import Iterable


FORBIDDEN_CURRENT_MATCH_FIELDS = {
    "winner_team_id",
    "kills",
    "deaths",
    "duration",
    "result",
    "draft",
    "drafts",
    "picks",
    "bans",
}


class LeakageError(ValueError):
    pass


def assert_no_forbidden_fields(fields: Iterable[str]) -> None:
    forbidden = FORBIDDEN_CURRENT_MATCH_FIELDS.intersection(set(fields))
    if forbidden:
        raise LeakageError(f"Forbidden pre-match fields used: {', '.join(sorted(forbidden))}")


def assert_match_not_current(match_id: int, candidate_match_id: int) -> None:
    if match_id == candidate_match_id:
        raise LeakageError("Current match cannot be used to build its own pre-match features")


def assert_before_cutoff(candidate_start_time: datetime | None, cutoff: datetime | None) -> None:
    if cutoff is None:
        raise LeakageError("Cannot use historical match with unknown current match start_time")
    if candidate_start_time is None:
        raise LeakageError("Cannot use historical match with unknown start_time")
    if candidate_start_time >= cutoff:
        raise LeakageError("Historical data after current match start_time is not allowed")


def validate_prematch_inputs(current_match, historical_matches) -> None:
    assert_no_forbidden_fields([])
    for match in historical_matches:
        assert_match_not_current(current_match.id, match.id)
        assert_before_cutoff(match.start_time, current_match.start_time)
