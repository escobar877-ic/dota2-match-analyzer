"""Allow immutable forecast snapshots after a schedule change.

Revision ID: 0015_rescheduled_forecasts
Revises: 0014_forecast_horizons
"""

from alembic import op


revision = "0015_rescheduled_forecasts"
down_revision = "0014_forecast_horizons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_prediction_forecasts_match_horizon",
        "prediction_forecasts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_horizon_schedule",
        "prediction_forecasts",
        ["match_id", "horizon_bucket", "scheduled_start"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_prediction_forecasts_match_horizon_schedule",
        "prediction_forecasts",
        type_="unique",
    )
    op.execute(
        """
        DELETE FROM prediction_forecasts older
        USING prediction_forecasts newer
        WHERE older.match_id = newer.match_id
          AND older.horizon_bucket = newer.horizon_bucket
          AND older.id < newer.id
        """
    )
    op.execute(
        """
        UPDATE prediction_forecasts
        SET is_primary = (horizon_bucket = 'final')
        """
    )
    op.create_unique_constraint(
        "uq_prediction_forecasts_match_horizon",
        "prediction_forecasts",
        ["match_id", "horizon_bucket"],
    )
