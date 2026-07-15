from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from app.database import SessionLocal
from app.db.models import DotaPatch, Match, MatchPatchContext


def _default_config_path() -> Path:
    cwd_path = Path.cwd() / "config" / "dota_patches.json"
    if cwd_path.exists():
        return cwd_path
    return Path(__file__).resolve().parents[3] / "config" / "dota_patches.json"


def load_patch_config(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or _default_config_path()
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list):
        raise ValueError("Patch config must be a list.")
    return payload


def sync_patches_from_config(db: Session | None = None, path: Path | None = None) -> dict[str, int]:
    owns_session = db is None
    db = db or SessionLocal()
    created = 0
    updated = 0
    try:
        for item in load_patch_config(path):
            release_date = _parse_release_date(item["release_date"])
            existing = db.scalar(select(DotaPatch).where(DotaPatch.patch_version == str(item["patch_version"])))
            if existing:
                existing.patch_name = str(item["patch_name"])
                existing.release_date = release_date
                existing.is_current = bool(item.get("is_current", False))
                updated += 1
            else:
                db.add(
                    DotaPatch(
                        patch_name=str(item["patch_name"]),
                        patch_version=str(item["patch_version"]),
                        release_date=release_date,
                        is_current=bool(item.get("is_current", False)),
                    )
                )
                created += 1
        db.commit()
        return {"created": created, "updated": updated}
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def get_patch_for_match(db: Session, match_start_time: datetime | None) -> DotaPatch | None:
    if match_start_time is None:
        return get_current_patch(db)
    match_start_time = _normalize_datetime(match_start_time)
    return db.scalar(
        select(DotaPatch)
        .where(DotaPatch.release_date <= match_start_time)
        .order_by(DotaPatch.release_date.desc(), DotaPatch.id.desc())
        .limit(1)
    )


def get_current_patch(db: Session) -> DotaPatch | None:
    patch = db.scalar(select(DotaPatch).where(DotaPatch.is_current.is_(True)).order_by(DotaPatch.release_date.desc()).limit(1))
    if patch is not None:
        return patch
    return db.scalar(select(DotaPatch).order_by(DotaPatch.release_date.desc(), DotaPatch.id.desc()).limit(1))


def calculate_days_since_patch(db: Session, match_start_time: datetime | None) -> int | None:
    patch = get_patch_for_match(db, match_start_time)
    if patch is None or match_start_time is None:
        return None
    return max(0, (_normalize_datetime(match_start_time).date() - patch.release_date.date()).days)


def upsert_match_patch_context(db: Session, match: Match) -> MatchPatchContext | None:
    patch = get_patch_for_match(db, match.start_time)
    if patch is None or match.start_time is None:
        return None
    days_since_patch = calculate_days_since_patch(db, match.start_time) or 0
    existing = db.scalar(select(MatchPatchContext).where(MatchPatchContext.match_id == match.id))
    if existing:
        existing.patch_id = patch.id
        existing.days_since_patch = days_since_patch
        existing.is_current_patch = patch.is_current
        return existing
    created = MatchPatchContext(
        match_id=match.id,
        patch_id=patch.id,
        days_since_patch=days_since_patch,
        is_current_patch=patch.is_current,
    )
    db.add(created)
    db.flush()
    return created


def _parse_release_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _normalize_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-config", action="store_true")
    args = parser.parse_args()
    if args.sync_config:
        result = sync_patches_from_config()
        print(f"Patch config sync complete: created={result['created']}, updated={result['updated']}")


if __name__ == "__main__":
    main()
