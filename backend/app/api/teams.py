from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Team
from app.ratings.rating_service import get_team_elo_rating, to_rating_read
from app.ratings.schemas import TeamRatingRead
from app.schemas.team import TeamDetail, TeamRead


router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamRead])
def list_teams(db: Session = Depends(get_db)) -> list[Team]:
    return with_db_error_handling(
        lambda: list(db.scalars(select(Team).order_by(Team.name)).all())
    )


@router.get("/{team_id}", response_model=TeamDetail)
def get_team(team_id: int, db: Session = Depends(get_db)) -> Team:
    team = with_db_error_handling(
        lambda: db.scalar(
            select(Team)
            .options(selectinload(Team.players))
            .where(Team.id == team_id)
        )
    )
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.get("/{team_id}/rating", response_model=TeamRatingRead)
def get_team_rating(team_id: int, db: Session = Depends(get_db)) -> TeamRatingRead:
    team_exists = with_db_error_handling(
        lambda: db.scalar(select(Team.id).where(Team.id == team_id))
    )
    if team_exists is None:
        raise HTTPException(status_code=404, detail="Team not found")

    rating = with_db_error_handling(lambda: get_team_elo_rating(db, team_id))
    if rating is None:
        raise HTTPException(status_code=404, detail="Team rating not found")
    return to_rating_read(rating)
