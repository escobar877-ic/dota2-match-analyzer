from __future__ import annotations

from pydantic import BaseModel, Field


class Tier1TeamConfig(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    region: str | None = None
    active: bool = True


class Tier1TournamentConfig(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    tier: int = 1
    active: bool = True


class Tier1Config(BaseModel):
    teams: list[Tier1TeamConfig]
    tournaments: list[Tier1TournamentConfig]
