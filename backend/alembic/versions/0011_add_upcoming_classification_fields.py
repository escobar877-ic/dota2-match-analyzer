"""Add upcoming match classification fields.

Revision ID: 0011_upcoming_classification
Revises: 0010_create_draft_tables
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_upcoming_classification"
down_revision = "0010_create_draft_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("dataset_profile", sa.String(length=32), nullable=True))
    op.add_column("matches", sa.Column("competition_tier", sa.String(length=32), nullable=True))
    op.add_column("matches", sa.Column("verification_status", sa.String(length=32), nullable=True))
    op.add_column("matches", sa.Column("source_confidence", sa.String(length=32), nullable=True))
    op.add_column("matches", sa.Column("is_training_eligible", sa.Boolean(), nullable=True))
    op.add_column("matches", sa.Column("is_prediction_eligible", sa.Boolean(), nullable=True))
    op.add_column("matches", sa.Column("prediction_block_reason", sa.Text(), nullable=True))
    op.add_column("matches", sa.Column("prediction_guard_level", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "prediction_guard_level")
    op.drop_column("matches", "prediction_block_reason")
    op.drop_column("matches", "is_prediction_eligible")
    op.drop_column("matches", "is_training_eligible")
    op.drop_column("matches", "source_confidence")
    op.drop_column("matches", "verification_status")
    op.drop_column("matches", "competition_tier")
    op.drop_column("matches", "dataset_profile")
