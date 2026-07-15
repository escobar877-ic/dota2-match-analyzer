from __future__ import annotations

from datetime import datetime, timezone

from worker.data_ingestion.clients import get_clients
from worker.data_ingestion.db import get_session, upsert_player
from worker.data_ingestion.normalizer import (
    NormalizedPlayer,
    normalize_opendota_players,
    normalize_pandascore_players,
)
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log
from sqlalchemy import select
from app.db.models import Team


def sync_players() -> dict[str, int]:
    created = 0
    updated = 0
    skipped_sources = 0

    db = get_session()
    try:
        for client in get_clients():
            started_at = datetime.now(timezone.utc)
            counters = SyncCounters()
            if not client.enabled:
                skipped_sources += 1
                print(f"{client.source_name}: disabled")
                write_sync_log(
                    db,
                    source=client.source_name,
                    sync_type="players",
                    status="failed",
                    started_at=started_at,
                    counters=counters,
                    error_message=f"{client.source_name.upper()} client disabled",
                )
                continue

            players, error = _fetch_players(db, client)
            if players is None:
                skipped_sources += 1
                write_sync_log(
                    db,
                    source=client.source_name,
                    sync_type="players",
                    status="failed",
                    started_at=started_at,
                    counters=counters,
                    error_message=error,
                )
                continue

            counters.records_seen = len(players)
            for player in players:
                _, was_created = upsert_player(db, player)
                created += int(was_created)
                updated += int(not was_created)
                counters.records_created += int(was_created)
                counters.records_updated += int(not was_created)
            write_sync_log(
                db,
                source=client.source_name,
                sync_type="players",
                status="ok",
                started_at=started_at,
                counters=counters,
            )

        db.commit()
        print(f"Players sync complete: created={created}, updated={updated}, skipped_sources={skipped_sources}")
        return {"created": created, "updated": updated, "skipped_sources": skipped_sources}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _fetch_players(db, client) -> tuple[list[NormalizedPlayer] | None, str | None]:
    if isinstance(client, PandaScoreClient):
        response = client.get_players()
        if not response.ok:
            print(f"{client.source_name}: {response.error}")
            return None, response.error
        return normalize_pandascore_players(response.data), None

    if isinstance(client, OpenDotaClient):
        players: list[NormalizedPlayer] = []
        teams = db.scalars(select(Team).where(Team.external_source == "opendota")).all()
        if not teams:
            print("opendota: no synced teams available for player sync")
            return [], None
        for team in teams[:5]:
            if not team.external_id:
                continue
            response = client.get_team_players(team.external_id)
            if not response.ok:
                print(f"opendota team {team.external_id}: {response.error}")
                continue
            players.extend(normalize_opendota_players(response.data, team.external_id))
        return players, None

    print(f"{client.source_name}: player sync is not implemented for this source")
    return [], None


if __name__ == "__main__":
    sync_players()
