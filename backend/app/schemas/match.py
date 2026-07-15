from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.team import TeamRead


class TeamMatchStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    team_id: int
    side: str | None
    kills: int | None
    deaths: int | None
    assists: int | None
    gold_diff_10: int | None
    xp_diff_10: int | None
    duration: int | None
    result: str | None
    created_at: datetime


class PredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    team_a_probability: float
    team_b_probability: float
    confidence: float
    explanation_json: dict[str, Any] | None
    model_type: str
    model_version: str
    created_at: datetime


class MatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_source: str | None
    external_id: str | None
    team_a_id: int
    team_b_id: int
    tournament_name: str | None
    tournament_tier: str | None
    start_time: datetime | None
    format: str | None
    status: str
    winner_team_id: int | None
    is_draw: bool
    is_tier1_match: bool
    excluded_reason: str | None
    dataset_profile: str | None
    competition_tier: str | None
    verification_status: str | None
    source_confidence: str | None
    is_training_eligible: bool | None
    is_prediction_eligible: bool | None
    prediction_block_reason: str | None
    prediction_guard_level: str | None
    created_at: datetime
    updated_at: datetime
    team_a: TeamRead
    team_b: TeamRead


class MatchDetail(MatchRead):
    stats: list[TeamMatchStatsRead] = []
    predictions: list[PredictionRead] = []
