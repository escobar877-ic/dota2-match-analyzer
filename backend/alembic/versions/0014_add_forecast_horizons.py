"""Add immutable multi-horizon prospective forecasts.

Revision ID: 0014_forecast_horizons
Revises: 0013_prediction_forecasts
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_forecast_horizons"
down_revision = "0013_prediction_forecasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prediction_forecasts",
        sa.Column("horizon_bucket", sa.String(length=32), nullable=False, server_default="early"),
    )
    op.add_column(
        "prediction_forecasts",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        """
        UPDATE prediction_forecasts
        SET horizon_bucket = CASE
                WHEN lead_time_hours <= 2 THEN 'final'
                WHEN lead_time_hours <= 24 THEN 'day_before'
                ELSE 'early'
            END,
            is_primary = CASE WHEN lead_time_hours <= 2 THEN true ELSE false END
        """
    )
    op.drop_constraint("uq_prediction_forecasts_match_id", "prediction_forecasts", type_="unique")
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_horizon",
        "prediction_forecasts",
        ["match_id", "horizon_bucket"],
    )
    op.create_index(
        "ix_prediction_forecasts_horizon_bucket",
        "prediction_forecasts",
        ["horizon_bucket"],
    )


def downgrade() -> None:
    op.drop_index("ix_prediction_forecasts_horizon_bucket", table_name="prediction_forecasts")
    op.drop_constraint(
        "uq_prediction_forecasts_match_horizon",
        "prediction_forecasts",
        type_="unique",
    )
    op.execute(
        """
        DELETE FROM prediction_forecasts newer
        USING prediction_forecasts older
        WHERE newer.match_id = older.match_id
          AND (
            CASE newer.horizon_bucket
              WHEN 'final' THEN 3
              WHEN 'day_before' THEN 2
              ELSE 1
            END
          ) < (
            CASE older.horizon_bucket
              WHEN 'final' THEN 3
              WHEN 'day_before' THEN 2
              ELSE 1
            END
          )
        """
    )
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_id",
        "prediction_forecasts",
        ["match_id"],
    )
    op.drop_column("prediction_forecasts", "is_primary")
    op.drop_column("prediction_forecasts", "horizon_bucket")
