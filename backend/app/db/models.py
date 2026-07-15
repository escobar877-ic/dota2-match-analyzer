from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active_tier1: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    players: Mapped[list["Player"]] = relationship(back_populates="team")
    ratings: Mapped[list["TeamRating"]] = relationship(back_populates="team")
    roster_entries: Mapped[list["TeamRoster"]] = relationship(back_populates="team")


class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nickname: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    real_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)

    team: Mapped[Team | None] = relationship(back_populates="players")
    roster_entries: Mapped[list["TeamRoster"]] = relationship(back_populates="player")


class Match(Base, TimestampMixin):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    team_b_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    tournament_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tournament_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_tier1_match: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    competition_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_training_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_prediction_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    prediction_block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    prediction_guard_level: Mapped[str | None] = mapped_column(String(32), nullable=True)

    team_a: Mapped[Team] = relationship(foreign_keys=[team_a_id])
    team_b: Mapped[Team] = relationship(foreign_keys=[team_b_id])
    winner_team: Mapped[Team | None] = relationship(foreign_keys=[winner_team_id])
    stats: Mapped[list["TeamMatchStats"]] = relationship(back_populates="match")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="match")
    patch_context: Mapped["MatchPatchContext | None"] = relationship(back_populates="match")
    draft_entries: Mapped[list["MatchDraft"]] = relationship(back_populates="match")
    draft_snapshots: Mapped[list["DraftSnapshot"]] = relationship(back_populates="match")


class TeamMatchStats(Base):
    __tablename__ = "team_match_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gold_diff_10: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xp_diff_10: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    match: Mapped[Match] = relationship(back_populates="stats")
    team: Mapped[Team] = relationship()


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_a_probability: Mapped[float] = mapped_column(Float, nullable=False)
    team_b_probability: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    explanation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    match: Mapped[Match] = relationship(back_populates="predictions")


class TeamRating(Base):
    __tablename__ = "team_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    rating_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rating_value: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty: Mapped[float] = mapped_column(Float, nullable=False)
    matches_count: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    team: Mapped[Team] = relationship(back_populates="ratings")


class MatchPrematchFeature(Base):
    __tablename__ = "match_features_prematch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    team_b_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    match: Mapped[Match] = relationship()
    team_a: Mapped[Team] = relationship(foreign_keys=[team_a_id])
    team_b: Mapped[Team] = relationship(foreign_keys=[team_b_id])


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    train_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    train_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    test_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    test_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate", server_default="candidate", nullable=False, index=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promotion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    matches_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    model_version: Mapped[ModelVersion | None] = relationship()


class DataSyncLog(Base):
    __tablename__ = "data_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sync_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    records_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    records_excluded: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class MarketOddsSnapshot(Base):
    __tablename__ = "market_odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(128), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    match: Mapped[Match] = relationship()


class PaperBet(Base):
    __tablename__ = "paper_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    no_vig_probability: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    stake_units: Mapped[float] = mapped_column(Float, default=1.0, server_default="1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending", nullable=False, index=True)
    profit_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    guard_reasons_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    match: Mapped[Match] = relationship()


class PredictionForecast(Base):
    __tablename__ = "prediction_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "horizon_bucket",
            "scheduled_start",
            name="uq_prediction_forecasts_match_horizon_schedule",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    horizon_bucket: Mapped[str] = mapped_column(
        String(32),
        default="early",
        server_default="early",
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lead_time_hours: Mapped[float] = mapped_column(Float, nullable=False)
    prediction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    team_a_probability: Mapped[float] = mapped_column(Float, nullable=False)
    team_b_probability: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_outcomes_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    components_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    guard_reasons_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending", nullable=False, index=True)
    actual_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    match: Mapped[Match] = relationship()


class TeamRoster(Base, TimestampMixin):
    __tablename__ = "team_rosters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False, index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)

    team: Mapped[Team] = relationship(back_populates="roster_entries")
    player: Mapped[Player] = relationship(back_populates="roster_entries")


class DotaPatch(Base, TimestampMixin):
    __tablename__ = "dota_patches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patch_name: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    release_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False, index=True)

    match_contexts: Mapped[list["MatchPatchContext"]] = relationship(back_populates="patch")


class MatchPatchContext(Base):
    __tablename__ = "match_patch_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, unique=True, index=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("dota_patches.id"), nullable=False, index=True)
    days_since_patch: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current_patch: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    match: Mapped[Match] = relationship(back_populates="patch_context")
    patch: Mapped[DotaPatch] = relationship(back_populates="match_contexts")


class Hero(Base, TimestampMixin):
    __tablename__ = "heroes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hero_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    localized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    primary_attr: Mapped[str | None] = mapped_column(String(32), nullable=True)
    roles_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False, index=True)

    draft_entries: Mapped[list["MatchDraft"]] = relationship(back_populates="hero")


class MatchDraft(Base):
    __tablename__ = "match_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    pick_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ban_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    draft_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown", nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    match: Mapped[Match] = relationship(back_populates="draft_entries")
    team: Mapped[Team] = relationship()
    hero: Mapped[Hero] = relationship(back_populates="draft_entries")
    player: Mapped[Player | None] = relationship()


class DraftSnapshot(Base):
    __tablename__ = "draft_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    draft_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    team_a_picks_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    team_b_picks_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    team_a_bans_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    team_b_bans_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    match: Mapped[Match] = relationship(back_populates="draft_snapshots")
