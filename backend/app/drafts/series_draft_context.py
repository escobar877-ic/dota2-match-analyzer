from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Match
from app.drafts.draft_service import draft_to_dict
from app.ratings.team_identity import resolve_scoped_team_identity_ids


SERIES_LOOKBACK = timedelta(minutes=45)
SERIES_LOOKAHEAD = timedelta(hours=12)
MAP_SOURCES = {"csv_import", "opendota", "stratz"}


def build_series_draft_context(db: Session, series_match: Match) -> dict[str, Any] | None:
    """Link a schedule-level series to stored map drafts using strict identities only."""
    if series_match.start_time is None or not series_match.tournament_name:
        return None

    team_a_ids = resolve_scoped_team_identity_ids(db, series_match.team_a_id)
    team_b_ids = resolve_scoped_team_identity_ids(db, series_match.team_b_id)
    statement = (
        select(Match)
        .options(
            selectinload(Match.team_a),
            selectinload(Match.team_b),
            selectinload(Match.winner_team),
        )
        .where(
            Match.id != series_match.id,
            Match.external_source.in_(MAP_SOURCES),
            Match.external_id.is_not(None),
            Match.start_time >= series_match.start_time - SERIES_LOOKBACK,
            Match.start_time <= series_match.start_time + SERIES_LOOKAHEAD,
            Match.tournament_name == series_match.tournament_name,
            Match.draft_entries.any(),
            or_(
                Match.team_a_id.in_(team_a_ids) & Match.team_b_id.in_(team_b_ids),
                Match.team_a_id.in_(team_b_ids) & Match.team_b_id.in_(team_a_ids),
            ),
        )
        .order_by(Match.start_time.asc(), Match.id.asc())
    )
    candidates = list(db.scalars(statement).all())
    maximum_maps = _maximum_maps(series_match.format)
    if not candidates:
        return None
    if len(candidates) > maximum_maps:
        return {
            "mapping_status": "ambiguous",
            "source": "verified_stored_maps",
            "map_count": 0,
            "maps": [],
            "source_note": (
                "Multiple stored maps match this series window, so no draft was linked automatically."
            ),
        }

    maps: list[dict[str, Any]] = []
    for game_number, map_match in enumerate(candidates, start=1):
        draft = draft_to_dict(db, map_match)
        if not draft["draft_available"]:
            continue
        entries = [
            {
                **entry,
                "team_id": _series_team_id(
                    int(entry["team_id"]),
                    series_match=series_match,
                    team_a_ids=team_a_ids,
                    team_b_ids=team_b_ids,
                ),
            }
            for entry in draft["entries"]
        ]
        winner_team_id = (
            _series_team_id(
                map_match.winner_team_id,
                series_match=series_match,
                team_a_ids=team_a_ids,
                team_b_ids=team_b_ids,
            )
            if map_match.winner_team_id is not None
            else None
        )
        maps.append(
            {
                **draft,
                "entries": entries,
                "database_match_id": map_match.id,
                "dota_match_id": map_match.external_id,
                "game_number": game_number,
                "start_time": map_match.start_time.isoformat() if map_match.start_time else None,
                "status": map_match.status,
                "winner_team_id": winner_team_id,
                "winner_team_name": map_match.winner_team.name if map_match.winner_team else None,
            }
        )
    if not maps:
        return None
    return {
        "mapping_status": "matched",
        "source": "verified_stored_maps",
        "map_count": len(maps),
        "maps": maps,
        "source_note": (
            "Map drafts are linked by verified canonical team identities, tournament and series time window. "
            "They are display-only and are not added to the pre-match prediction."
        ),
    }


def _maximum_maps(match_format: str | None) -> int:
    normalized = str(match_format or "").upper()
    return {"BO1": 1, "BO2": 2, "BO3": 3, "BO5": 5}.get(normalized, 5)


def _series_team_id(
    source_team_id: int,
    *,
    series_match: Match,
    team_a_ids: set[int],
    team_b_ids: set[int],
) -> int:
    if source_team_id in team_a_ids:
        return series_match.team_a_id
    if source_team_id in team_b_ids:
        return series_match.team_b_id
    return source_team_id
