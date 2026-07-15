from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DataSyncLog


@dataclass(frozen=True)
class SourceStatus:
    enabled: bool
    has_api_key: bool
    last_sync_status: str
    last_error: str | None


SOURCE_KEYS = {
    "opendota": "OPENDOTA_API_KEY",
    "stratz": "STRATZ_API_KEY",
    "pandascore": "PANDASCORE_API_KEY",
}


def get_source_statuses(db: Session | None = None) -> dict[str, SourceStatus]:
    return {source: get_source_status(source, db=db) for source in SOURCE_KEYS}


def get_source_status(source: str, db: Session | None = None) -> SourceStatus:
    env_key = SOURCE_KEYS[source]
    has_api_key = bool(os.getenv(env_key))
    enabled = source == "opendota" or has_api_key
    last_log = _latest_log(db, source) if db is not None else None
    missing_key_error = f"{env_key} missing" if not has_api_key and source != "opendota" else None
    return SourceStatus(
        enabled=enabled,
        has_api_key=has_api_key,
        last_sync_status=last_log.status if last_log else "never",
        last_error=(last_log.error_message if last_log and last_log.error_message else missing_key_error),
    )


def _latest_log(db: Session, source: str) -> DataSyncLog | None:
    return db.scalar(
        select(DataSyncLog)
        .where(DataSyncLog.source == source)
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .limit(1)
    )
