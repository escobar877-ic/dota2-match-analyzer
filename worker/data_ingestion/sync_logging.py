from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import DataSyncLog


@dataclass
class SyncCounters:
    records_seen: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_excluded: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def write_sync_log(
    db: Session,
    *,
    source: str,
    sync_type: str,
    status: str,
    started_at: datetime,
    counters: SyncCounters | None = None,
    error_message: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> DataSyncLog:
    counters = counters or SyncCounters()
    metadata = dict(counters.metadata)
    if metadata_json:
        metadata.update(metadata_json)
    log = DataSyncLog(
        source=source,
        sync_type=sync_type,
        status=status,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        records_seen=counters.records_seen,
        records_created=counters.records_created,
        records_updated=counters.records_updated,
        records_excluded=counters.records_excluded,
        error_message=error_message,
        metadata_json=metadata or None,
    )
    db.add(log)
    db.commit()
    return log
