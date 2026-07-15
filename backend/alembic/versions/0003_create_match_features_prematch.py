"""create match prematch features

Revision ID: 0003_prematch_features
Revises: 0002_create_team_ratings
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_prematch_features"
down_revision = "0002_create_team_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_features_prematch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_a_id", sa.Integer(), nullable=False),
        sa.Column("team_b_id", sa.Integer(), nullable=False),
        sa.Column("feature_version", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["team_a_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["team_b_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_match_features_prematch_feature_version"), "match_features_prematch", ["feature_version"], unique=False)
    op.create_index(op.f("ix_match_features_prematch_id"), "match_features_prematch", ["id"], unique=False)
    op.create_index(op.f("ix_match_features_prematch_match_id"), "match_features_prematch", ["match_id"], unique=False)
    op.create_index(op.f("ix_match_features_prematch_team_a_id"), "match_features_prematch", ["team_a_id"], unique=False)
    op.create_index(op.f("ix_match_features_prematch_team_b_id"), "match_features_prematch", ["team_b_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_match_features_prematch_team_b_id"), table_name="match_features_prematch")
    op.drop_index(op.f("ix_match_features_prematch_team_a_id"), table_name="match_features_prematch")
    op.drop_index(op.f("ix_match_features_prematch_match_id"), table_name="match_features_prematch")
    op.drop_index(op.f("ix_match_features_prematch_id"), table_name="match_features_prematch")
    op.drop_index(op.f("ix_match_features_prematch_feature_version"), table_name="match_features_prematch")
    op.drop_table("match_features_prematch")
