"""Track strict and preview forecasts independently.

Revision ID: 0016_forecast_evaluation_scope
Revises: 0015_rescheduled_forecasts
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_forecast_evaluation_scope"
down_revision = "0015_rescheduled_forecasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prediction_forecasts",
        sa.Column(
            "evaluation_scope",
            sa.String(length=32),
            nullable=False,
            server_default="strict_tier1",
        ),
    )
    op.execute(
        """
        UPDATE prediction_forecasts
        SET evaluation_scope = CASE
            WHEN prediction_type = 'verified_pro_preview' THEN 'verified_pro_preview'
            ELSE 'strict_tier1'
        END
        """
    )
    op.drop_constraint(
        "uq_prediction_forecasts_match_horizon_schedule",
        "prediction_forecasts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_horizon_schedule_scope",
        "prediction_forecasts",
        ["match_id", "horizon_bucket", "scheduled_start", "evaluation_scope"],
    )
    op.create_index(
        "ix_prediction_forecasts_evaluation_scope",
        "prediction_forecasts",
        ["evaluation_scope"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prediction_forecasts_evaluation_scope",
        table_name="prediction_forecasts",
    )
    op.drop_constraint(
        "uq_prediction_forecasts_match_horizon_schedule_scope",
        "prediction_forecasts",
        type_="unique",
    )
    op.execute(
        """
        DELETE FROM prediction_forecasts preview
        USING prediction_forecasts strict
        WHERE preview.match_id = strict.match_id
          AND preview.horizon_bucket = strict.horizon_bucket
          AND preview.scheduled_start = strict.scheduled_start
          AND preview.evaluation_scope = 'verified_pro_preview'
          AND strict.evaluation_scope = 'strict_tier1'
        """
    )
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_horizon_schedule",
        "prediction_forecasts",
        ["match_id", "horizon_bucket", "scheduled_start"],
    )
    op.drop_column("prediction_forecasts", "evaluation_scope")
