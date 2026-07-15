from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone

from worker.data_ingestion.clients import get_clients
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.data_quality import validate_match
from worker.data_ingestion.normalizer import (
    NormalizedMatch,
    normalize_opendota_matches,
    normalize_pandascore_matches,
    normalize_stratz_matches,
)
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log
from app.tier_filter.tier1_matcher import Tier1Matcher


def sync_matches(*, dry_run: bool = False) -> dict[str, int]:
    created = 0
    updated = 0
    excluded = 0
    seen = 0
    skipped_sources = 0
    exclusion_reasons: Counter[str] = Counter()

    db = get_session()
    try:
        matcher = Tier1Matcher()
        for client in get_clients():
            started_at = datetime.now(timezone.utc)
            counters = SyncCounters()
            if not client.enabled:
                skipped_sources += 1
                error = f"{client.source_name.upper()} client disabled"
                print(f"{client.source_name}: disabled")
                if not dry_run:
                    write_sync_log(
                        db,
                        source=client.source_name,
                        sync_type="matches",
                        status="failed",
                        started_at=started_at,
                        counters=counters,
                        error_message=error,
                    )
                continue

            responses = [client.get_matches()]
            upcoming = client.get_upcoming_matches()
            if upcoming.ok:
                responses.append(upcoming)
            elif upcoming.error:
                print(f"{client.source_name}: upcoming skipped: {upcoming.error}")

            for response in responses:
                if not response.ok:
                    skipped_sources += 1
                    print(f"{client.source_name}: {response.error}")
                    if not dry_run:
                        write_sync_log(
                            db,
                            source=client.source_name,
                            sync_type="matches",
                            status="failed",
                            started_at=started_at,
                            counters=counters,
                            error_message=response.error,
                        )
                    continue

                matches = _normalize(client.source_name, response.data)
                counters.records_seen += len(matches)
                seen += len(matches)
                for match in matches:
                    quality = validate_match(match, matcher=matcher)
                    if dry_run:
                        if quality.reasons:
                            excluded += 1
                            counters.records_excluded += 1
                            exclusion_reasons.update(quality.reasons)
                        else:
                            created += 1
                            counters.records_created += 1
                        continue

                    db_match, was_created = upsert_match(db, match, matcher=matcher)
                    if db_match is None:
                        excluded += 1
                        counters.records_excluded += 1
                        exclusion_reasons.update(quality.reasons or ["not_tier1_match"])
                    else:
                        created += int(was_created)
                        updated += int(not was_created)
                        counters.records_created += int(was_created)
                        counters.records_updated += int(not was_created)

                if not dry_run:
                    write_sync_log(
                        db,
                        source=client.source_name,
                        sync_type="matches",
                        status="ok",
                        started_at=started_at,
                        counters=counters,
                        metadata_json={"upcoming_error": upcoming.error},
                    )

        if dry_run:
            db.rollback()
            print(
                "Matches dry-run complete: "
                f"records_seen={seen}, would_create={created}, would_update={updated}, "
                f"would_exclude={excluded}, exclusion_reasons={dict(exclusion_reasons)}"
            )
        else:
            db.commit()
            print(
                f"Matches sync complete: created={created}, updated={updated}, "
                f"excluded={excluded}, skipped_sources={skipped_sources}"
            )
        return {
            "records_seen": seen,
            "created": created,
            "updated": updated,
            "excluded": excluded,
            "skipped_sources": skipped_sources,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _normalize(source_name: str, payload) -> list[NormalizedMatch]:
    if source_name == "opendota":
        return normalize_opendota_matches(payload)
    if source_name == "pandascore":
        return normalize_pandascore_matches(payload)
    if source_name == "stratz":
        return normalize_stratz_matches(payload)
    return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sync_matches(dry_run=args.dry_run)
