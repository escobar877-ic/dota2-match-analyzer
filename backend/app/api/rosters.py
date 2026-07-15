from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Team
from app.rosters.roster_service import (
    get_active_roster,
    get_recent_standins_count,
    get_roster_stability_days,
    get_same_roster_matches_count,
    has_recent_roster_change,
)
from app.rosters.schemas import TeamRosterContext


router = APIRouter(prefix="/rosters", tags=["rosters"])


@router.get("/teams/{team_id}", response_model=TeamRosterContext)
def get_team_roster(team_id: int, db: Session = Depends(get_db)) -> TeamRosterContext:
    team_exists = with_db_error_handling(lambda: db.scalar(select(Team.id).where(Team.id == team_id)))
    if team_exists is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return with_db_error_handling(
        lambda: TeamRosterContext(
            team_id=team_id,
            active_roster=get_active_roster(db, team_id),
            roster_stability_days=get_roster_stability_days(db, team_id, None),
            same_roster_matches_count=get_same_roster_matches_count(db, team_id, None),
            has_recent_roster_change=has_recent_roster_change(db, team_id, None),
            recent_standins_count=get_recent_standins_count(db, team_id, None),
        )
    )
