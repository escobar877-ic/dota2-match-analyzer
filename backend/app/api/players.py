from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Player
from app.schemas.player import PlayerRead


router = APIRouter(prefix="/players", tags=["players"])


@router.get("", response_model=list[PlayerRead])
def list_players(db: Session = Depends(get_db)) -> list[Player]:
    return with_db_error_handling(
        lambda: list(db.scalars(select(Player).order_by(Player.nickname)).all())
    )


@router.get("/{player_id}", response_model=PlayerRead)
def get_player(player_id: int, db: Session = Depends(get_db)) -> Player:
    player = with_db_error_handling(
        lambda: db.scalar(select(Player).where(Player.id == player_id))
    )
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return player
