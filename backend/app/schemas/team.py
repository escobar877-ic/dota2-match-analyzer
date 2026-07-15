from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlayerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    real_name: str | None
    role: str | None
    country: str | None


class TeamBase(BaseModel):
    external_source: str | None
    external_id: str | None
    name: str
    logo_url: str | None
    country: str | None
    region: str | None
    tier: str | None
    is_active_tier1: bool
    excluded_reason: str | None


class TeamRead(TeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class TeamDetail(TeamRead):
    players: list[PlayerSummary] = []
