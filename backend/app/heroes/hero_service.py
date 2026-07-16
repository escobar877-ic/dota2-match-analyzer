from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db.models import Hero


HERO_CONFIG_PATH = Path("config/dota_heroes.json")


def load_hero_config(path: str | Path = HERO_CONFIG_PATH) -> list[dict]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    return json.loads(config_path.read_text(encoding="utf-8"))


def sync_heroes_from_config(db: Session, path: str | Path = HERO_CONFIG_PATH) -> dict[str, int]:
    return sync_heroes_from_records(db, load_hero_config(path))


def sync_heroes_from_records(
    db: Session,
    records: list[dict[str, Any]],
    *,
    commit: bool = True,
) -> dict[str, int]:
    created = 0
    updated = 0
    for item in records:
        external_hero_id = int(item["hero_id"])
        hero = db.scalar(select(Hero).where(Hero.hero_id == external_hero_id))
        if hero is None:
            hero = Hero(hero_id=external_hero_id, name=item["name"], localized_name=item["localized_name"])
            db.add(hero)
            created += 1
        else:
            incoming = (
                item["name"],
                item["localized_name"],
                item.get("primary_attr"),
                item.get("roles") or item.get("roles_json") or [],
                bool(item.get("is_active", True)),
            )
            current = (
                hero.name,
                hero.localized_name,
                hero.primary_attr,
                hero.roles_json or [],
                hero.is_active,
            )
            updated += int(current != incoming)
        hero.name = item["name"]
        hero.localized_name = item["localized_name"]
        hero.primary_attr = item.get("primary_attr")
        hero.roles_json = item.get("roles") or item.get("roles_json") or []
        hero.is_active = bool(item.get("is_active", True))
    if commit:
        db.commit()
    return {"created": created, "updated": updated}


def list_heroes(db: Session) -> list[Hero]:
    return list(db.scalars(select(Hero).order_by(Hero.localized_name.asc(), Hero.id.asc())).all())


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Dota heroes from local config.")
    parser.add_argument("--sync-config", action="store_true")
    args = parser.parse_args()
    if not args.sync_config:
        parser.print_help()
        return
    db = SessionLocal()
    try:
        result = sync_heroes_from_config(db)
        print(f"Hero config sync complete: created={result['created']}, updated={result['updated']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
