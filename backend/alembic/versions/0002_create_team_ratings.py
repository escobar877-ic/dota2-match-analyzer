"""create team ratings

Revision ID: 0002_create_team_ratings
Revises: 0001_create_core_tables
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_create_team_ratings"
down_revision = "0001_create_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_ratings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("rating_type", sa.String(length=64), nullable=False),
        sa.Column("rating_value", sa.Float(), nullable=False),
        sa.Column("uncertainty", sa.Float(), nullable=False),
        sa.Column("matches_count", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_ratings_id"), "team_ratings", ["id"], unique=False)
    op.create_index(op.f("ix_team_ratings_rating_type"), "team_ratings", ["rating_type"], unique=False)
    op.create_index(op.f("ix_team_ratings_team_id"), "team_ratings", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_team_ratings_team_id"), table_name="team_ratings")
    op.drop_index(op.f("ix_team_ratings_rating_type"), table_name="team_ratings")
    op.drop_index(op.f("ix_team_ratings_id"), table_name="team_ratings")
    op.drop_table("team_ratings")
