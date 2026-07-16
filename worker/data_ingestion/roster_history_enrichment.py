from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Match, Player, TeamRoster
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session
from worker.data_ingestion.match_detail_enrichment import (
    _fetch_match_detail,
    _is_radiant_player,
    _validate_detail,
)
from worker.data_ingestion.opendota_detail_cache import (
    DEFAULT_CACHE_DIR,
    load_cached_match_detail,
    write_cached_match_detail,
)
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


ROSTER_SOURCE = "opendota_history"
REPORT_PATH = Path(ML_ARTIFACT_DIR) / "roster_history_enrichment_report.json"
CACHE_DIR = DEFAULT_CACHE_DIR


@dataclass(frozen=True)
class PlayerObservation:
    external_id: str
    nickname: str


@dataclass(frozen=True)
class RosterObservation:
    team_id: int
    match_id: int
    observed_at: datetime
    players: tuple[PlayerObservation, ...]


@dataclass(frozen=True)
class RosterSegment:
    team_id: int
    start_date: datetime
    end_date: datetime
    players: tuple[PlayerObservation, ...]
    matches_observed: int


def enrich_roster_history(
    *,
    apply: bool = False,
    limit: int = 1000,
    sleep_seconds: float = 1.0,
    max_gap_days: int = 45,
    tournament: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    cache_only: bool = False,
    merge_only: bool = False,
    refresh_cache: bool = False,
    external_sources: set[str] | None = None,
    external_ids: list[str] | None = None,
    recent_first: bool = False,
    detail_cache_dir: str | Path | None = None,
    client: OpenDotaClient | None = None,
    artifact_path: str | Path | None = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    db = get_session()
    client = client or OpenDotaClient()
    counters = SyncCounters()
    warnings: list[str] = []
    errors: list[str] = []
    observations: list[RosterObservation] = []
    cache_hits = 0
    cache_misses = 0
    fetched = 0
    incomplete_rosters = 0
    try:
        source_scope = sorted(external_sources or {"csv_import"})
        statement = select(Match).options(selectinload(Match.team_a), selectinload(Match.team_b)).where(
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.is_tier1_match.is_(True),
            Match.external_source.in_(source_scope),
            Match.dataset_profile == "historical_training",
            Match.verification_status == "verified",
            Match.source_confidence.in_(["high", "medium"]),
            Match.external_id.is_not(None),
            Match.start_time.is_not(None),
        )
        if tournament:
            statement = statement.where(Match.tournament_name.ilike(f"%{tournament.strip()}%"))
        if external_ids is not None:
            statement = statement.where(Match.external_id.in_(list(dict.fromkeys(external_ids))))
        if start_date:
            statement = statement.where(Match.start_time >= start_date)
        if end_date:
            statement = statement.where(Match.start_time < end_date)
        order = (
            (Match.start_time.desc(), Match.id.desc())
            if recent_first
            else (Match.start_time.asc(), Match.id.asc())
        )
        matches = list(
            db.scalars(
                statement
                .order_by(*order)
                .limit(max(1, min(int(limit), 2000)))
            ).all()
        )
        counters.records_seen = len(matches)
        for index, match in enumerate(matches):
            raw, from_cache, fetch_error = _load_match_detail(
                client,
                match.external_id or "",
                refresh_cache=refresh_cache,
                cache_only=cache_only,
                cache_dir=detail_cache_dir,
            )
            cache_hits += int(from_cache)
            fetched += int(raw is not None and not from_cache)
            if raw is None:
                if cache_only and fetch_error == "detail cache miss":
                    cache_misses += 1
                    continue
                counters.records_excluded += 1
                errors.append(f"match_id={match.external_id}: {fetch_error or 'detail unavailable'}")
                continue
            validation_errors, side_to_team_id = _validate_detail(match, raw)
            if validation_errors:
                counters.records_excluded += 1
                warnings.append(f"match_id={match.external_id}: {','.join(validation_errors)}")
                continue
            players = raw.get("players") if isinstance(raw.get("players"), list) else []
            for side, team_id in side_to_team_id.items():
                side_players = [
                    player
                    for player in players
                    if _is_radiant_player(player) == (side == "radiant")
                ]
                roster = extract_player_observations(side_players)
                if roster is None:
                    incomplete_rosters += 1
                    continue
                observations.append(
                    RosterObservation(
                        team_id=team_id,
                        match_id=match.id,
                        # The match itself cannot use a roster learned from its post-match details.
                        observed_at=match.start_time + timedelta(seconds=1),
                        players=roster,
                    )
                )
            if index < len(matches) - 1 and not from_cache and sleep_seconds > 0:
                time.sleep(sleep_seconds)

        segments = build_roster_segments(observations, max_gap_days=max_gap_days)
        roster_rows_created = 0
        roster_rows_updated = 0
        players_created = 0
        if apply and segments:
            result = apply_roster_segments(
                db,
                segments,
                replace_start=start_date,
                replace_end=end_date,
                merge_only=merge_only,
            )
            roster_rows_created = result["roster_rows_created"]
            roster_rows_updated = result["roster_rows_updated"]
            players_created = result["players_created"]
            counters.records_updated = len({observation.match_id for observation in observations})
            write_sync_log(
                db,
                source="opendota",
                sync_type="roster_history_enrichment",
                status="warning" if errors else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(errors[:20]) if errors else None,
                metadata_json={
                    "observations": len(observations),
                    "segments": len(segments),
                    "cache_hits": cache_hits,
                    "cache_misses": cache_misses,
                    "fetched": fetched,
                    "max_gap_days": max_gap_days,
                    "cache_only": cache_only,
                    "merge_only": merge_only,
                    "no_future_data": True,
                    "training_changed": False,
                    "promotion_changed": False,
                },
            )
            db.commit()
        else:
            db.rollback()

        report = {
            "status": "warning" if errors or incomplete_rosters else "ok",
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "apply" if apply else "dry_run",
            "filters": {
                "tournament": tournament,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "external_sources": source_scope,
                "external_ids_count": len(external_ids) if external_ids is not None else None,
                "recent_first": recent_first,
            },
            "cache_only": cache_only,
            "merge_only": merge_only,
            "records_seen": counters.records_seen,
            "matches_with_roster_observations": len({item.match_id for item in observations}),
            "team_roster_observations": len(observations),
            "roster_segments": len(segments),
            "incomplete_team_rosters": incomplete_rosters,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "details_fetched": fetched,
            "records_excluded": counters.records_excluded,
            "players_created": players_created,
            "roster_rows_created": roster_rows_created,
            "roster_rows_updated": roster_rows_updated,
            "roster_rows_invalidated": result.get("roster_rows_invalidated", 0) if apply and segments else 0,
            "roster_rows_truncated": result.get("roster_rows_truncated", 0) if apply and segments else 0,
            "roster_rows_merged": result.get("roster_rows_merged", 0) if apply and segments else 0,
            "max_gap_days": max_gap_days,
            "source": "opendota_match_details",
            "roster_source": ROSTER_SOURCE,
            "no_future_data": True,
            "hard_delete_used": False,
            "training_changed": False,
            "promotion_changed": False,
            "warnings": _unique(warnings),
            "errors": errors[:100],
            "recommendation": "apply_after_review" if not apply and segments else "rebuild_features_after_review",
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def extract_player_observations(
    players: list[dict[str, Any]],
) -> tuple[PlayerObservation, ...] | None:
    by_id: dict[str, PlayerObservation] = {}
    for player in players:
        try:
            account_id = int(player.get("account_id") or 0)
        except (TypeError, ValueError):
            continue
        if account_id <= 0:
            continue
        external_id = str(account_id)
        nickname = str(player.get("personaname") or player.get("name") or f"player_{external_id}").strip()
        by_id[external_id] = PlayerObservation(external_id=external_id, nickname=nickname[:255])
    if len(by_id) != 5:
        return None
    return tuple(by_id[key] for key in sorted(by_id, key=int))


def build_roster_segments(
    observations: list[RosterObservation],
    *,
    max_gap_days: int = 45,
) -> list[RosterSegment]:
    by_team: dict[int, list[RosterObservation]] = {}
    for observation in observations:
        by_team.setdefault(observation.team_id, []).append(observation)
    segments: list[RosterSegment] = []
    max_gap = timedelta(days=max(1, max_gap_days))
    for team_id, team_observations in sorted(by_team.items()):
        ordered = sorted(team_observations, key=lambda item: (item.observed_at, item.match_id))
        current_start: datetime | None = None
        current_last: datetime | None = None
        current_players: tuple[PlayerObservation, ...] | None = None
        matches_observed = 0
        for observation in ordered:
            same_players = _player_ids(observation.players) == _player_ids(current_players or ())
            within_gap = current_last is not None and observation.observed_at - current_last <= max_gap
            if current_players is not None and (not same_players or not within_gap):
                segments.append(
                    RosterSegment(
                        team_id=team_id,
                        start_date=current_start,
                        end_date=min(current_last + max_gap, observation.observed_at),
                        players=current_players,
                        matches_observed=matches_observed,
                    )
                )
                current_start = None
            if current_start is None:
                current_start = observation.observed_at
                current_players = observation.players
                matches_observed = 0
            current_last = observation.observed_at
            matches_observed += 1
        if current_players is not None and current_start is not None and current_last is not None:
            segments.append(
                RosterSegment(
                    team_id=team_id,
                    start_date=current_start,
                    end_date=current_last + max_gap,
                    players=current_players,
                    matches_observed=matches_observed,
                )
            )
    return segments


def apply_roster_segments(
    db,
    segments: list[RosterSegment],
    *,
    replace_start: datetime | None = None,
    replace_end: datetime | None = None,
    merge_only: bool = False,
) -> dict[str, int]:
    team_ids = sorted({segment.team_id for segment in segments})
    existing_rows = list(
        db.scalars(
            select(TeamRoster).where(
                TeamRoster.team_id.in_(team_ids),
                TeamRoster.source == ROSTER_SOURCE,
            )
        ).all()
    )
    roster_rows_invalidated = 0
    roster_rows_truncated = 0
    roster_rows_merged = 0
    if not merge_only:
        for row in existing_rows:
            action = _replacement_action(
                row,
                replace_start=replace_start,
                replace_end=replace_end,
            )
            if action == "preserve":
                continue
            row.is_active = False
            if action == "truncate":
                row.end_date = replace_start
                roster_rows_truncated += 1
            else:
                row.end_date = row.start_date
                roster_rows_invalidated += 1
    else:
        (
            segments,
            roster_rows_truncated,
            roster_rows_invalidated,
            roster_rows_merged,
        ) = _merge_incremental_segments(db, segments, existing_rows)

    players_created = 0
    roster_rows_created = 0
    roster_rows_updated = 0
    for segment in segments:
        for player_observation in segment.players:
            player = db.scalar(
                select(Player)
                .where(
                    Player.external_source == "opendota",
                    Player.external_id == player_observation.external_id,
                )
                .limit(1)
            )
            if player is None:
                player = Player(
                    external_source="opendota",
                    external_id=player_observation.external_id,
                    nickname=player_observation.nickname,
                )
                db.add(player)
                db.flush()
                players_created += 1
            elif player.nickname.startswith("player_") and not player_observation.nickname.startswith("player_"):
                player.nickname = player_observation.nickname

            roster = db.scalar(
                select(TeamRoster)
                .where(
                    TeamRoster.team_id == segment.team_id,
                    TeamRoster.player_id == player.id,
                    TeamRoster.start_date == segment.start_date,
                    TeamRoster.source == ROSTER_SOURCE,
                )
                .limit(1)
            )
            if roster is None:
                roster = TeamRoster(
                    team_id=segment.team_id,
                    player_id=player.id,
                    start_date=segment.start_date,
                    source=ROSTER_SOURCE,
                )
                db.add(roster)
                roster_rows_created += 1
            else:
                roster_rows_updated += 1
            roster.end_date = segment.end_date
            roster.is_active = False
    return {
        "players_created": players_created,
        "roster_rows_created": roster_rows_created,
        "roster_rows_updated": roster_rows_updated,
        "roster_rows_invalidated": roster_rows_invalidated,
        "roster_rows_truncated": roster_rows_truncated,
        "roster_rows_merged": roster_rows_merged,
    }


def _merge_incremental_segments(
    db,
    segments: list[RosterSegment],
    existing_rows: list[TeamRoster],
) -> tuple[list[RosterSegment], int, int, int]:
    grouped: dict[tuple[int, datetime, datetime | None], list[TeamRoster]] = {}
    for row in existing_rows:
        if row.start_date is None:
            continue
        key = (row.team_id, _aware(row.start_date), _aware(row.end_date) if row.end_date else None)
        grouped.setdefault(key, []).append(row)

    existing_segments: dict[int, list[tuple[datetime, datetime, tuple[str, ...], list[TeamRoster]]]] = {}
    for (team_id, start_date, end_date), rows in grouped.items():
        resolved_end = end_date or datetime.max.replace(tzinfo=UTC)
        player_ids = tuple(
            sorted(
                (
                    str(player.external_id)
                    for row in rows
                    if (player := db.get(Player, row.player_id)) is not None and player.external_id
                ),
                key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
            )
        )
        existing_segments.setdefault(team_id, []).append((start_date, resolved_end, player_ids, rows))

    pending: list[RosterSegment] = []
    truncated = 0
    invalidated = 0
    merged = 0
    for segment in sorted(segments, key=lambda item: (item.team_id, item.start_date, item.end_date)):
        incoming_ids = _player_ids(segment.players)
        incoming_end = segment.end_date
        merged_into_existing = False
        for old_start, old_end, old_ids, rows in sorted(
            existing_segments.get(segment.team_id, []), key=lambda item: item[0]
        ):
            if old_end <= segment.start_date or old_start >= incoming_end:
                continue
            if old_ids == incoming_ids:
                extended_start = min(old_start, segment.start_date)
                extended_end = max(old_end, incoming_end)
                for row in rows:
                    if _aware(row.start_date) != extended_start or row.end_date is None or _aware(row.end_date) != extended_end:
                        row.start_date = extended_start
                        row.end_date = extended_end
                        merged += 1
                merged_into_existing = True
                break
            if old_start < segment.start_date:
                for row in rows:
                    row.end_date = segment.start_date
                    row.is_active = False
                    truncated += 1
            elif old_start == segment.start_date:
                for row in rows:
                    row.end_date = old_start
                    row.is_active = False
                    invalidated += 1
            else:
                incoming_end = min(incoming_end, old_start)
        if not merged_into_existing and incoming_end > segment.start_date:
            pending.append(
                RosterSegment(
                    team_id=segment.team_id,
                    start_date=segment.start_date,
                    end_date=incoming_end,
                    players=segment.players,
                    matches_observed=segment.matches_observed,
                )
            )
    return pending, truncated, invalidated, merged


def _replacement_action(
    row: TeamRoster,
    *,
    replace_start: datetime | None,
    replace_end: datetime | None,
) -> str:
    if replace_start is None and replace_end is None:
        return "invalidate"
    row_start = _aware(row.start_date)
    row_end = _aware(row.end_date) if row.end_date else datetime.max.replace(tzinfo=UTC)
    window_start = _aware(replace_start) if replace_start else datetime.min.replace(tzinfo=UTC)
    window_end = _aware(replace_end) if replace_end else datetime.max.replace(tzinfo=UTC)
    if row_end <= window_start or row_start >= window_end:
        return "preserve"
    if row_start < window_start:
        return "truncate"
    return "invalidate"


def _load_match_detail(
    client: OpenDotaClient,
    match_id: str,
    *,
    refresh_cache: bool,
    cache_only: bool = False,
    cache_dir: str | Path | None = None,
) -> tuple[dict[str, Any] | None, bool, str | None]:
    if not refresh_cache:
        raw = load_cached_match_detail(match_id, cache_dir=cache_dir)
        if raw is not None:
            return raw, True, None
    if cache_only:
        return None, False, "detail cache miss"
    response, _, _ = _fetch_match_detail(
        client,
        match_id,
        max_retries=2,
        backoff_seconds=30.0,
    )
    if not response.ok or not isinstance(response.data, dict):
        return None, False, response.error or "OpenDota detail response is invalid."
    try:
        write_cached_match_detail(match_id, response.data, cache_dir=cache_dir)
    except (OSError, ValueError) as exc:
        return None, False, f"detail cache write failed: {exc}"
    return response.data, False, None


def _player_ids(players: tuple[PlayerObservation, ...]) -> tuple[str, ...]:
    return tuple(player.external_id for player in players)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build leakage-safe historical roster segments from OpenDota match details."
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--tournament")
    parser.add_argument("--start-date", type=_parse_date_arg)
    parser.add_argument("--end-date", type=_parse_date_arg)
    parser.add_argument("--max-gap-days", type=int, default=45)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument(
        "--external-source",
        action="append",
        dest="external_sources",
        help="Trusted match source to include; repeat for multiple sources (default: csv_import).",
    )
    parser.add_argument(
        "--external-id",
        action="append",
        dest="external_ids",
        help="Exact Dota match id to include; repeat for multiple ids.",
    )
    parser.add_argument("--recent-first", action="store_true")
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Upsert derived segments without invalidating existing generated history.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    enrich_roster_history(
        apply=args.apply,
        limit=args.limit,
        sleep_seconds=max(0.0, args.sleep),
        max_gap_days=max(1, args.max_gap_days),
        tournament=args.tournament,
        start_date=args.start_date,
        end_date=args.end_date,
        cache_only=args.cache_only,
        merge_only=args.merge_only,
        refresh_cache=args.refresh_cache,
        external_sources=set(args.external_sources) if args.external_sources else None,
        external_ids=args.external_ids,
        recent_first=args.recent_first,
    )


def _parse_date_arg(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO date or datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    main()
