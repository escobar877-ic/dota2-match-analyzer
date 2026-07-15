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
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


ROSTER_SOURCE = "opendota_history"
REPORT_PATH = Path(ML_ARTIFACT_DIR) / "roster_history_enrichment_report.json"
CACHE_DIR = Path(ML_ARTIFACT_DIR) / "source_cache" / "opendota_matches"


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
    refresh_cache: bool = False,
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
    fetched = 0
    incomplete_rosters = 0
    try:
        matches = list(
            db.scalars(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(
                    Match.status == "finished",
                    Match.winner_team_id.is_not(None),
                    Match.is_tier1_match.is_(True),
                    Match.external_source == "csv_import",
                    Match.dataset_profile == "historical_training",
                    Match.verification_status == "verified",
                    Match.source_confidence.in_(["high", "medium"]),
                    Match.external_id.is_not(None),
                    Match.start_time.is_not(None),
                )
                .order_by(Match.start_time.asc(), Match.id.asc())
                .limit(max(1, min(int(limit), 2000)))
            ).all()
        )
        counters.records_seen = len(matches)
        for index, match in enumerate(matches):
            raw, from_cache, fetch_error = _load_match_detail(
                client,
                match.external_id or "",
                refresh_cache=refresh_cache,
            )
            cache_hits += int(from_cache)
            fetched += int(raw is not None and not from_cache)
            if raw is None:
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
            result = apply_roster_segments(db, segments)
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
                    "fetched": fetched,
                    "max_gap_days": max_gap_days,
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
            "records_seen": counters.records_seen,
            "matches_with_roster_observations": len({item.match_id for item in observations}),
            "team_roster_observations": len(observations),
            "roster_segments": len(segments),
            "incomplete_team_rosters": incomplete_rosters,
            "cache_hits": cache_hits,
            "details_fetched": fetched,
            "records_excluded": counters.records_excluded,
            "players_created": players_created,
            "roster_rows_created": roster_rows_created,
            "roster_rows_updated": roster_rows_updated,
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


def apply_roster_segments(db, segments: list[RosterSegment]) -> dict[str, int]:
    team_ids = sorted({segment.team_id for segment in segments})
    existing_rows = list(
        db.scalars(
            select(TeamRoster).where(
                TeamRoster.team_id.in_(team_ids),
                TeamRoster.source == ROSTER_SOURCE,
            )
        ).all()
    )
    # Soft-invalidate obsolete generated rows; rebuild below without hard delete.
    for row in existing_rows:
        row.is_active = False
        row.end_date = row.start_date

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
    }


def _load_match_detail(
    client: OpenDotaClient,
    match_id: str,
    *,
    refresh_cache: bool,
) -> tuple[dict[str, Any] | None, bool, str | None]:
    cache_path = CACHE_DIR / f"{match_id}.json"
    if not refresh_cache and cache_path.exists():
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(raw.get("match_id") or "") == str(match_id):
                return raw, True, None
        except (OSError, json.JSONDecodeError):
            pass
    response, _, _ = _fetch_match_detail(
        client,
        match_id,
        max_retries=2,
        backoff_seconds=30.0,
    )
    if not response.ok or not isinstance(response.data, dict):
        return None, False, response.error or "OpenDota detail response is invalid."
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(response.data, sort_keys=True), encoding="utf-8")
    temporary.replace(cache_path)
    return response.data, False, None


def _player_ids(players: tuple[PlayerObservation, ...]) -> tuple[str, ...]:
    return tuple(player.external_id for player in players)


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
    parser.add_argument("--max-gap-days", type=int, default=45)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    enrich_roster_history(
        apply=args.apply,
        limit=args.limit,
        sleep_seconds=max(0.0, args.sleep),
        max_gap_days=max(1, args.max_gap_days),
        refresh_cache=args.refresh_cache,
    )


if __name__ == "__main__":
    main()
