from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.heroes.hero_service import list_heroes


router = APIRouter(prefix="/heroes", tags=["heroes"])


@router.get("")
def get_heroes(db: Session = Depends(get_db)) -> list[dict]:
    return with_db_error_handling(lambda: [_hero_to_dict(hero) for hero in list_heroes(db)])


def _hero_to_dict(hero) -> dict:
    return {
        "id": hero.id,
        "hero_id": hero.hero_id,
        "name": hero.name,
        "localized_name": hero.localized_name,
        "primary_attr": hero.primary_attr,
        "roles_json": hero.roles_json,
        "is_active": hero.is_active,
    }
