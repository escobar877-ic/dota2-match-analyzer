from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select


backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from app.db.models import Hero
from app.heroes.hero_service import sync_heroes_from_records
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session
from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "hero_constants_sync_report.json"


def sync_hero_constants(
    *,
    apply: bool = False,
    client: OpenDotaClient | None = None,
    artifact_path: str | Path | None = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    client = client or OpenDotaClient()
    db = get_session()
    errors: list[str] = []
    warnings: list[str] = []
    counters = SyncCounters()
    placeholders_replaced = 0
    try:
        response = client.get_heroes()
        if not response.ok:
            errors.append(response.error or "OpenDota hero constants request failed.")
            report = _report(
                apply=apply,
                counters=counters,
                placeholders_replaced=0,
                errors=errors,
                warnings=warnings,
            )
            _write_report(report, artifact_path)
            print(json.dumps(report, indent=2, sort_keys=True))
            return report

        records = normalize_hero_constants(response.data)
        counters.records_seen = len(records)
        existing = {
            hero.hero_id: hero
            for hero in db.scalars(select(Hero).where(Hero.hero_id.in_([row["hero_id"] for row in records]))).all()
        }
        placeholders_replaced = sum(
            1
            for row in records
            if row["hero_id"] in existing
            and _is_placeholder(existing[row["hero_id"]])
            and not _record_is_placeholder(row)
        )
        would_create = sum(1 for row in records if row["hero_id"] not in existing)
        would_update = sum(
            1
            for row in records
            if row["hero_id"] in existing and _hero_would_change(existing[row["hero_id"]], row)
        )

        if apply:
            result = sync_heroes_from_records(db, records, commit=False)
            counters.records_created = result["created"]
            counters.records_updated = result["updated"]
            write_sync_log(
                db,
                source="opendota",
                sync_type="hero_constants",
                status="ok",
                started_at=started_at,
                counters=counters,
                metadata_json={"placeholders_replaced": placeholders_replaced},
            )
            db.commit()
        else:
            db.rollback()

        report = _report(
            apply=apply,
            counters=counters,
            placeholders_replaced=placeholders_replaced,
            errors=errors,
            warnings=warnings,
            would_create=would_create,
            would_update=would_update,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return report
    except Exception as exc:
        db.rollback()
        report = _report(
            apply=apply,
            counters=counters,
            placeholders_replaced=0,
            errors=[f"{exc.__class__.__name__}: {exc}"],
            warnings=warnings,
        )
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return report
    finally:
        db.close()


def normalize_hero_constants(payload: Any) -> list[dict[str, Any]]:
    values = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    records: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in values:
        if not isinstance(raw, dict):
            continue
        try:
            hero_id = int(raw.get("id") or raw.get("hero_id") or 0)
        except (TypeError, ValueError):
            continue
        name = str(raw.get("name") or "").strip()
        localized_name = str(raw.get("localized_name") or "").strip()
        if hero_id <= 0 or hero_id in seen or not name or not localized_name:
            continue
        seen.add(hero_id)
        records.append(
            {
                "hero_id": hero_id,
                "name": name,
                "localized_name": localized_name,
                "primary_attr": raw.get("primary_attr"),
                "roles": list(raw.get("roles") or []),
                "is_active": True,
            }
        )
    return sorted(records, key=lambda item: item["hero_id"])


def _is_placeholder(hero: Hero) -> bool:
    return hero.localized_name == f"Hero {hero.hero_id}" or hero.name == f"hero_{hero.hero_id}"


def _record_is_placeholder(record: dict[str, Any]) -> bool:
    return record["localized_name"] == f"Hero {record['hero_id']}"


def _hero_would_change(hero: Hero, record: dict[str, Any]) -> bool:
    return (
        hero.name,
        hero.localized_name,
        hero.primary_attr,
        hero.roles_json or [],
        hero.is_active,
    ) != (
        record["name"],
        record["localized_name"],
        record.get("primary_attr"),
        record.get("roles") or [],
        bool(record.get("is_active", True)),
    )


def _report(
    *,
    apply: bool,
    counters: SyncCounters,
    placeholders_replaced: int,
    errors: list[str],
    warnings: list[str],
    would_create: int = 0,
    would_update: int = 0,
) -> dict[str, Any]:
    return {
        "status": "failed" if errors else "warning" if warnings else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if apply else "dry_run",
        "source": "opendota",
        "records_seen": counters.records_seen,
        "would_create": would_create if not apply else 0,
        "would_update": would_update if not apply else 0,
        "records_created": counters.records_created if apply else 0,
        "records_updated": counters.records_updated if apply else 0,
        "placeholders_replaced": placeholders_replaced,
        "errors": errors,
        "warnings": warnings,
        "training_changed": False,
        "promotion_changed": False,
    }


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync complete Dota hero constants from OpenDota.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync_hero_constants(apply=args.apply)


if __name__ == "__main__":
    main()
