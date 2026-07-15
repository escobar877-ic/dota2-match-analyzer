"""create roster and patch tables

Revision ID: 0008_create_roster_patch_tables
Revises: 0007_create_data_sync_logs
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_create_roster_patch_tables"
down_revision = "0007_create_data_sync_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_rosters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_rosters_id"), "team_rosters", ["id"], unique=False)
    op.create_index(op.f("ix_team_rosters_is_active"), "team_rosters", ["is_active"], unique=False)
    op.create_index(op.f("ix_team_rosters_player_id"), "team_rosters", ["player_id"], unique=False)
    op.create_index(op.f("ix_team_rosters_start_date"), "team_rosters", ["start_date"], unique=False)
    op.create_index(op.f("ix_team_rosters_team_id"), "team_rosters", ["team_id"], unique=False)

    op.create_table(
        "dota_patches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patch_name", sa.String(length=64), nullable=False),
        sa.Column("patch_version", sa.String(length=64), nullable=False),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patch_version"),
    )
    op.create_index(op.f("ix_dota_patches_id"), "dota_patches", ["id"], unique=False)
    op.create_index(op.f("ix_dota_patches_is_current"), "dota_patches", ["is_current"], unique=False)
    op.create_index(op.f("ix_dota_patches_release_date"), "dota_patches", ["release_date"], unique=False)

    op.create_table(
        "match_patch_context",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("patch_id", sa.Integer(), nullable=False),
        sa.Column("days_since_patch", sa.Integer(), nullable=False),
        sa.Column("is_current_patch", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["patch_id"], ["dota_patches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id"),
    )
    op.create_index(op.f("ix_match_patch_context_id"), "match_patch_context", ["id"], unique=False)
    op.create_index(op.f("ix_match_patch_context_match_id"), "match_patch_context", ["match_id"], unique=False)
    op.create_index(op.f("ix_match_patch_context_patch_id"), "match_patch_context", ["patch_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_match_patch_context_patch_id"), table_name="match_patch_context")
    op.drop_index(op.f("ix_match_patch_context_match_id"), table_name="match_patch_context")
    op.drop_index(op.f("ix_match_patch_context_id"), table_name="match_patch_context")
    op.drop_table("match_patch_context")
    op.drop_index(op.f("ix_dota_patches_release_date"), table_name="dota_patches")
    op.drop_index(op.f("ix_dota_patches_is_current"), table_name="dota_patches")
    op.drop_index(op.f("ix_dota_patches_id"), table_name="dota_patches")
    op.drop_table("dota_patches")
    op.drop_index(op.f("ix_team_rosters_team_id"), table_name="team_rosters")
    op.drop_index(op.f("ix_team_rosters_start_date"), table_name="team_rosters")
    op.drop_index(op.f("ix_team_rosters_player_id"), table_name="team_rosters")
    op.drop_index(op.f("ix_team_rosters_is_active"), table_name="team_rosters")
    op.drop_index(op.f("ix_team_rosters_id"), table_name="team_rosters")
    op.drop_table("team_rosters")
