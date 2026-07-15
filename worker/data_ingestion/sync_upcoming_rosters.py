from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, Player, Team, TeamRoster
from app.prediction.verified_pro_preview import is_verified_pro_preview_match
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "upcoming_roster_sync_report.json"
SOURCE = "pandascore"


def sync_upcoming_rosters(*, dry_run: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    client = PandaScoreClient()
    started_at = datetime.now(timezone.utc)
    counters = SyncCounters()
    warnings: list[str] = []
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    teams_seen = 0
    complete_rosters = 0
    exact_five_rosters = 0
    ambiguous_rosters = 0
    incomplete_rosters = 0
    would_create_players = 0
    would_create_entries = 0
    changed_rosters = 0
    try:
        teams = _upcoming_actionable_teams(db)
        teams_seen = len(teams)
        for team in teams:
            if team.external_source != SOURCE or not team.external_id:
                warnings.append(f"{team.name}: missing PandaScore team id.")
                incomplete_rosters += 1
                continue
            response = client.get_team(team.external_id)
            if not response.ok or not isinstance(response.data, dict):
                errors.append(f"{team.name}: {response.error or 'invalid team response'}")
                incomplete_rosters += 1
                continue
            raw_players = [
                player
                for player in (response.data.get("players") or [])
                if isinstance(player, dict) and player.get("active", True) and player.get("id")
            ]
            counters.records_seen += len(raw_players)
            if len(raw_players) < 5:
                incomplete_rosters += 1
                warnings.append(f"{team.name}: only {len(raw_players)} active roster players returned.")
                continue
            complete_rosters += 1
            exact_five_rosters += int(len(raw_players) == 5)
            ambiguous_rosters += int(len(raw_players) > 5)
            incoming_ids = {str(player["id"]) for player in raw_players}
            current_entries = _current_source_entries(db, team.id)
            current_ids = {entry.player.external_id for entry in current_entries}
            roster_changed = bool(current_entries and current_ids != incoming_ids)
            changed_rosters += int(roster_changed)
            would_create_entries += len(incoming_ids - current_ids)
            would_create_players += sum(
                1
                for external_id in incoming_ids
                if _find_player(db, external_id) is None
            )
            if len(samples) < 20:
                samples.append(
                    {
                        "team_id": team.id,
                        "team": team.name,
                        "players_count": len(raw_players),
                        "roster_changed": roster_changed,
                        "players": [str(player.get("name") or player.get("id")) for player in raw_players],
                    }
                )
            if dry_run:
                continue
            _apply_team_roster(
                db,
                team,
                raw_players,
                current_entries,
                roster_changed=roster_changed,
            )

        if dry_run:
            db.rollback()
        else:
            counters.records_created = would_create_players + would_create_entries
            counters.records_updated = sum(sample["players_count"] for sample in samples) - would_create_players
            write_sync_log(
                db,
                source=SOURCE,
                sync_type="upcoming_rosters",
                status="warning" if errors or warnings else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(errors) if errors else None,
                metadata_json={
                    "teams_seen": teams_seen,
                    "complete_rosters": complete_rosters,
                    "exact_five_rosters": exact_five_rosters,
                    "ambiguous_rosters": ambiguous_rosters,
                    "incomplete_rosters": incomplete_rosters,
                    "changed_rosters": changed_rosters,
                },
            )
            db.commit()
        report = {
            "status": "warning" if errors or warnings else "ok",
            "mode": "dry_run" if dry_run else "apply",
            "teams_seen": teams_seen,
            "complete_rosters": complete_rosters,
            "exact_five_rosters": exact_five_rosters,
            "ambiguous_rosters": ambiguous_rosters,
            "incomplete_rosters": incomplete_rosters,
            "changed_rosters": changed_rosters,
            "players_seen": counters.records_seen,
            "would_create_players": would_create_players if dry_run else 0,
            "would_create_roster_entries": would_create_entries if dry_run else 0,
            "records_created": counters.records_created if not dry_run else 0,
            "errors": errors,
            "warnings": warnings,
            "samples": samples,
            "training_changed": False,
            "recommendation": (
                "review_then_apply" if dry_run and complete_rosters else "refresh_before_upcoming_matches"
            ),
        }
        _write_report(report)
        return report
    finally:
        db.close()


def _upcoming_actionable_teams(db) -> list[Team]:
    matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(
                Match.status.in_(["upcoming", "live"]),
                Match.external_source == SOURCE,
            )
        ).all()
    )
    actionable = [
        match
        for match in matches
        if match.is_tier1_match or is_verified_pro_preview_match(match, allow_finished=False)
    ]
    ids = {match.team_a_id for match in actionable} | {match.team_b_id for match in actionable}
    if not ids:
        return []
    return list(db.scalars(select(Team).where(Team.id.in_(ids)).order_by(Team.name)).all())


def _current_source_entries(db, team_id: int) -> list[TeamRoster]:
    return list(
        db.scalars(
            select(TeamRoster).where(
                TeamRoster.team_id == team_id,
                TeamRoster.source == SOURCE,
                TeamRoster.is_active.is_(True),
            )
        ).all()
    )


def _find_player(db, external_id: str) -> Player | None:
    return db.scalar(
        select(Player).where(
            Player.external_source == SOURCE,
            Player.external_id == external_id,
        )
    )


def _apply_team_roster(
    db,
    team: Team,
    raw_players: list[dict[str, Any]],
    current_entries: list[TeamRoster],
    *,
    roster_changed: bool,
) -> None:
    now = datetime.now(timezone.utc)
    incoming_ids = {str(player["id"]) for player in raw_players}
    if roster_changed:
        for entry in current_entries:
            if entry.player.external_id not in incoming_ids:
                entry.is_active = False
                entry.end_date = now

    current_by_external_id = {
        entry.player.external_id: entry for entry in current_entries if entry.is_active
    }
    for raw in raw_players:
        external_id = str(raw["id"])
        player = _find_player(db, external_id)
        if player is None:
            player = Player(
                external_source=SOURCE,
                external_id=external_id,
                nickname=str(raw.get("name") or external_id),
                real_name=_real_name(raw),
                team_id=team.id,
                role=str(raw.get("role")) if raw.get("role") is not None else None,
                country=raw.get("nationality"),
            )
            db.add(player)
            db.flush()
        else:
            player.nickname = str(raw.get("name") or player.nickname)
            player.real_name = _real_name(raw) or player.real_name
            player.team_id = team.id
            player.role = str(raw.get("role")) if raw.get("role") is not None else player.role
            player.country = raw.get("nationality") or player.country

        entry = current_by_external_id.get(external_id)
        if entry is None:
            db.add(
                TeamRoster(
                    team_id=team.id,
                    player_id=player.id,
                    role=player.role,
                    start_date=now if current_entries else None,
                    end_date=None,
                    is_active=True,
                    source=SOURCE,
                )
            )
        else:
            entry.role = player.role


def _real_name(raw: dict[str, Any]) -> str | None:
    value = " ".join(
        part.strip()
        for part in (str(raw.get("first_name") or ""), str(raw.get("last_name") or ""))
        if part.strip()
    )
    return value or None


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync current rosters for upcoming Tier 1 matches.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = sync_upcoming_rosters(dry_run=not args.apply)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
