from __future__ import annotations

from datetime import datetime, timezone

from worker.data_ingestion.clients import get_clients
from worker.data_ingestion.db import get_session, upsert_team
from worker.data_ingestion.normalizer import (
    NormalizedTeam,
    normalize_opendota_teams,
    normalize_pandascore_teams,
    normalize_stratz_teams,
)
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log
from app.tier_filter.tier1_matcher import Tier1Matcher


def sync_teams() -> dict[str, int]:
    created = 0
    updated = 0
    skipped_sources = 0

    db = get_session()
    try:
        matcher = Tier1Matcher()
        for client in get_clients():
            started_at = datetime.now(timezone.utc)
            counters = SyncCounters()
            if not client.enabled:
                skipped_sources += 1
                print(f"{client.source_name}: disabled")
                write_sync_log(
                    db,
                    source=client.source_name,
                    sync_type="teams",
                    status="failed",
                    started_at=started_at,
                    counters=counters,
                    error_message=f"{client.source_name.upper()} client disabled",
                )
                continue

            response = client.get_teams()
            if not response.ok:
                skipped_sources += 1
                print(f"{client.source_name}: {response.error}")
                write_sync_log(
                    db,
                    source=client.source_name,
                    sync_type="teams",
                    status="failed",
                    started_at=started_at,
                    counters=counters,
                    error_message=response.error,
                )
                continue

            teams = _normalize(client.source_name, response.data)
            counters.records_seen = len(teams)
            for team in teams:
                _, was_created = upsert_team(db, team, matcher=matcher)
                created += int(was_created)
                updated += int(not was_created)
                counters.records_created += int(was_created)
                counters.records_updated += int(not was_created)
                counters.records_excluded += int(not matcher.is_tier1_team(team.name))
            write_sync_log(
                db,
                source=client.source_name,
                sync_type="teams",
                status="ok",
                started_at=started_at,
                counters=counters,
            )

        db.commit()
        print(f"Teams sync complete: created={created}, updated={updated}, skipped_sources={skipped_sources}")
        return {"created": created, "updated": updated, "skipped_sources": skipped_sources}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _normalize(source_name: str, payload) -> list[NormalizedTeam]:
    if source_name == "opendota":
        return normalize_opendota_teams(payload)
    if source_name == "pandascore":
        return normalize_pandascore_teams(payload)
    if source_name == "stratz":
        return normalize_stratz_teams(payload)
    return []


if __name__ == "__main__":
    sync_teams()
