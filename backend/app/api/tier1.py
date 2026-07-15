from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Match, Team
from app.tier_filter.schemas import Tier1TeamConfig, Tier1TournamentConfig
from app.tier_filter.tier1_config_loader import load_tier1_config


router = APIRouter(prefix="/tier1", tags=["tier1"])


class Tier1StatusResponse(BaseModel):
    tier1_teams_count: int
    tier1_matches_count: int
    excluded_teams_count: int
    excluded_matches_count: int


@router.get("/status", response_model=Tier1StatusResponse)
def get_tier1_status(db: Session = Depends(get_db)) -> Tier1StatusResponse:
    return with_db_error_handling(lambda: _get_tier1_status(db))


@router.get("/teams", response_model=list[Tier1TeamConfig])
def get_tier1_teams() -> list[Tier1TeamConfig]:
    return load_tier1_config().teams


@router.get("/tournaments", response_model=list[Tier1TournamentConfig])
def get_tier1_tournaments() -> list[Tier1TournamentConfig]:
    return load_tier1_config().tournaments


def _get_tier1_status(db: Session) -> Tier1StatusResponse:
    tier1_teams_count = db.scalar(select(func.count()).select_from(Team).where(Team.is_active_tier1.is_(True))) or 0
    excluded_teams_count = db.scalar(select(func.count()).select_from(Team).where(Team.is_active_tier1.is_(False))) or 0
    tier1_matches_count = db.scalar(select(func.count()).select_from(Match).where(Match.is_tier1_match.is_(True))) or 0
    excluded_matches_count = db.scalar(select(func.count()).select_from(Match).where(Match.is_tier1_match.is_(False))) or 0

    return Tier1StatusResponse(
        tier1_teams_count=tier1_teams_count,
        tier1_matches_count=tier1_matches_count,
        excluded_teams_count=excluded_teams_count,
        excluded_matches_count=excluded_matches_count,
    )
