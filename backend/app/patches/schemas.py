from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DotaPatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patch_name: str
    patch_version: str
    release_date: datetime
    is_current: bool


class MatchPatchContextRead(BaseModel):
    patch: DotaPatchRead | None
    days_since_patch: int | None
    is_current_patch: bool
