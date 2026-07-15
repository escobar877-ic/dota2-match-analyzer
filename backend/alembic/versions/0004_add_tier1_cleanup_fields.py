"""add tier1 cleanup fields

Revision ID: 0004_tier1_cleanup_fields
Revises: 0003_prematch_features
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_tier1_cleanup_fields"
down_revision = "0003_prematch_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("tier", sa.String(length=64), nullable=True))
    op.add_column(
        "teams",
        sa.Column("is_active_tier1", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("teams", sa.Column("excluded_reason", sa.Text(), nullable=True))

    op.add_column(
        "matches",
        sa.Column("is_tier1_match", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("matches", sa.Column("excluded_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "excluded_reason")
    op.drop_column("matches", "is_tier1_match")
    op.drop_column("teams", "excluded_reason")
    op.drop_column("teams", "is_active_tier1")
    op.drop_column("teams", "tier")
