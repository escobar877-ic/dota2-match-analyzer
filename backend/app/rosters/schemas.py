from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RosterEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: int
    player_id: int
    role: str | None
    start_date: datetime | None
    end_date: datetime | None
    is_active: bool
    source: str | None


class TeamRosterContext(BaseModel):
    team_id: int
    active_roster: list[RosterEntryRead]
    roster_stability_days: int
    same_roster_matches_count: int
    has_recent_roster_change: bool
    recent_standins_count: int
