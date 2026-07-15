from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MarketEvaluationRequest(BaseModel):
    bookmaker: str = Field(default="manual", min_length=1, max_length=128)
    market_type: str = "auto"
    team_a_odds: float = Field(gt=1.0, le=1000.0)
    team_b_odds: float = Field(gt=1.0, le=1000.0)
    draw_odds: float | None = Field(default=None, gt=1.0, le=1000.0)
    captured_at: datetime | None = None


class OutcomeValue(BaseModel):
    outcome: str
    model_probability: float
    decimal_odds: float
    implied_probability: float
    no_vig_probability: float
    edge: float
    expected_value: float


class MarketEvaluationResponse(BaseModel):
    match_id: int
    bookmaker: str
    market_type: str
    overround: float
    outcomes: list[OutcomeValue]
    best_outcome: str | None
    paper_test_eligible: bool
    recommendation: str
    guard_reasons: list[str]
    paper_bet_id: int | None = None
    warning: str


class PaperBetRead(BaseModel):
    id: int
    match_id: int
    market_type: str
    outcome: str
    model_probability: float
    decimal_odds: float
    no_vig_probability: float
    edge: float
    expected_value: float
    stake_units: float
    status: str
    profit_units: float | None
    guard_reasons_json: list[str] | None
    created_at: datetime
    settled_at: datetime | None
