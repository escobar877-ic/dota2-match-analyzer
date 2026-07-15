"""Create prospective prediction forecasts and support drawn series.

Revision ID: 0013_prediction_forecasts
Revises: 0012_market_odds_paper_bets
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_prediction_forecasts"
down_revision = "0012_market_odds_paper_bets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("is_draw", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "prediction_forecasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_time_hours", sa.Float(), nullable=False),
        sa.Column("prediction_type", sa.String(length=32), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("team_a_probability", sa.Float(), nullable=False),
        sa.Column("team_b_probability", sa.Float(), nullable=False),
        sa.Column("confidence_label", sa.String(length=16), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("predicted_outcomes_json", sa.JSON(), nullable=False),
        sa.Column("components_json", sa.JSON(), nullable=True),
        sa.Column("guard_reasons_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("actual_outcome", sa.String(length=16), nullable=True),
        sa.Column("log_loss", sa.Float(), nullable=True),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("match_id", name="uq_prediction_forecasts_match_id"),
    )
    op.create_index("ix_prediction_forecasts_match_id", "prediction_forecasts", ["match_id"])
    op.create_index("ix_prediction_forecasts_status", "prediction_forecasts", ["status"])
    op.create_index("ix_prediction_forecasts_generated_at", "prediction_forecasts", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_prediction_forecasts_generated_at", table_name="prediction_forecasts")
    op.drop_index("ix_prediction_forecasts_status", table_name="prediction_forecasts")
    op.drop_index("ix_prediction_forecasts_match_id", table_name="prediction_forecasts")
    op.drop_table("prediction_forecasts")
    op.drop_column("matches", "is_draw")
