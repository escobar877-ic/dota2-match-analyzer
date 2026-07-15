from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_source: str | None
    external_id: str | None
    nickname: str
    real_name: str | None
    team_id: int | None
    role: str | None
    country: str | None
    created_at: datetime
    updated_at: datetime
