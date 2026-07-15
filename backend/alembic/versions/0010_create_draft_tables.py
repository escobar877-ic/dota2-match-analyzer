"""create draft tables

Revision ID: 0010_create_draft_tables
Revises: 0009_add_model_promotion_fields
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_create_draft_tables"
down_revision = "0009_add_model_promotion_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "heroes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("localized_name", sa.String(length=128), nullable=False),
        sa.Column("primary_attr", sa.String(length=32), nullable=True),
        sa.Column("roles_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hero_id"),
    )
    op.create_index(op.f("ix_heroes_hero_id"), "heroes", ["hero_id"], unique=False)
    op.create_index(op.f("ix_heroes_id"), "heroes", ["id"], unique=False)
    op.create_index(op.f("ix_heroes_is_active"), "heroes", ["is_active"], unique=False)

    op.create_table(
        "match_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("pick_order", sa.Integer(), nullable=True),
        sa.Column("ban_order", sa.Integer(), nullable=True),
        sa.Column("draft_order", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["hero_id"], ["heroes.id"]),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_match_drafts_action_type"), "match_drafts", ["action_type"], unique=False)
    op.create_index(op.f("ix_match_drafts_draft_order"), "match_drafts", ["draft_order"], unique=False)
    op.create_index(op.f("ix_match_drafts_hero_id"), "match_drafts", ["hero_id"], unique=False)
    op.create_index(op.f("ix_match_drafts_id"), "match_drafts", ["id"], unique=False)
    op.create_index(op.f("ix_match_drafts_match_id"), "match_drafts", ["match_id"], unique=False)
    op.create_index(op.f("ix_match_drafts_team_id"), "match_drafts", ["team_id"], unique=False)

    op.create_table(
        "draft_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("draft_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("team_a_picks_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("team_b_picks_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("team_a_bans_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("team_b_bans_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_draft_snapshots_id"), "draft_snapshots", ["id"], unique=False)
    op.create_index(op.f("ix_draft_snapshots_match_id"), "draft_snapshots", ["match_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_draft_snapshots_match_id"), table_name="draft_snapshots")
    op.drop_index(op.f("ix_draft_snapshots_id"), table_name="draft_snapshots")
    op.drop_table("draft_snapshots")
    op.drop_index(op.f("ix_match_drafts_team_id"), table_name="match_drafts")
    op.drop_index(op.f("ix_match_drafts_match_id"), table_name="match_drafts")
    op.drop_index(op.f("ix_match_drafts_id"), table_name="match_drafts")
    op.drop_index(op.f("ix_match_drafts_hero_id"), table_name="match_drafts")
    op.drop_index(op.f("ix_match_drafts_draft_order"), table_name="match_drafts")
    op.drop_index(op.f("ix_match_drafts_action_type"), table_name="match_drafts")
    op.drop_table("match_drafts")
    op.drop_index(op.f("ix_heroes_is_active"), table_name="heroes")
    op.drop_index(op.f("ix_heroes_id"), table_name="heroes")
    op.drop_index(op.f("ix_heroes_hero_id"), table_name="heroes")
    op.drop_table("heroes")
