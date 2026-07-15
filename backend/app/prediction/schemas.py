from typing import Any

from pydantic import BaseModel, ConfigDict


class PredictionFactors(BaseModel):
    recent_form: float
    team_rating: float
    head_to_head: float
    hero_pool: float
    roster_stability: float


class EnsembleComponent(BaseModel):
    available: bool
    team_a_probability: float | None = None
    weight: float = 0.0
    model_version: str | None = None
    unavailable_reason: str | None = None


class FormulaPredictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: str
    prediction_type: str
    model_version: str
    team_a_probability: float
    team_b_probability: float
    confidence: str
    confidence_score: float
    factors: PredictionFactors
    explanation: list[str] | dict[str, Any]
    warning: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    data_freshness: dict[str, str | None] | None = None
    components: dict[str, EnsembleComponent] | None = None
    weights: dict[str, float] | None = None
    component_summary: list[str] | None = None
    confidence_guard_applied: bool = False
    confidence_reasons: list[str] = []
    original_probability_before_guard: float | None = None
    weight_source: str | None = None
    weight_reason: str | None = None
    backtest_metrics_used: bool = False
    walk_forward_metrics_used: bool = False
    probability_unit: str = "map_strength"
    series_outcomes: dict[str, Any] | None = None
    analytics_context: dict[str, Any] | None = None


class TeamFeatureSnapshot(BaseModel):
    team_id: int
    recent_form: float
    rating: float
    hero_pool: float
    roster_stability: float
    matches_count: int
    roster_count: int
    stats_count: int
    elo_rating: float | None = None
    glicko_rating: float | None = None
    rating_uncertainty: float | None = None
    history_scope: str = "tier1"


class MatchFeatureSnapshot(BaseModel):
    match_id: int
    team_a: TeamFeatureSnapshot
    team_b: TeamFeatureSnapshot
    head_to_head: float
    head_to_head_count: int
