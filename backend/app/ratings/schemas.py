from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TeamRatingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    team_id: str
    rating_type: str
    rating_value: int
    uncertainty: float
    matches_count: int
    calculated_at: datetime


class EloState(BaseModel):
    rating: float = 1500.0
    matches_count: int = 0
    uncertainty: float = 350.0
