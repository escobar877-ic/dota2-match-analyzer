from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import DotaPatch
from app.patches.patch_service import get_current_patch
from app.patches.schemas import DotaPatchRead


router = APIRouter(prefix="/patches", tags=["patches"])


@router.get("", response_model=list[DotaPatchRead])
def list_patches(db: Session = Depends(get_db)) -> list[DotaPatch]:
    return with_db_error_handling(
        lambda: list(db.scalars(select(DotaPatch).order_by(DotaPatch.release_date.desc(), DotaPatch.id.desc())).all())
    )


@router.get("/current", response_model=DotaPatchRead)
def read_current_patch(db: Session = Depends(get_db)) -> DotaPatch:
    patch = with_db_error_handling(lambda: get_current_patch(db))
    if patch is None:
        raise HTTPException(status_code=404, detail="Current patch not found")
    return patch
