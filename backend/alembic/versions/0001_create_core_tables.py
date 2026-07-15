"""create core tables

Revision ID: 0001_create_core_tables
Revises:
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_source", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_teams_id"), "teams", ["id"], unique=False)
    op.create_index(op.f("ix_teams_name"), "teams", ["name"], unique=False)

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_source", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("nickname", sa.String(length=255), nullable=False),
        sa.Column("real_name", sa.String(length=255), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_players_id"), "players", ["id"], unique=False)
    op.create_index(op.f("ix_players_nickname"), "players", ["nickname"], unique=False)
    op.create_index(op.f("ix_players_team_id"), "players", ["team_id"], unique=False)

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_source", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("team_a_id", sa.Integer(), nullable=False),
        sa.Column("team_b_id", sa.Integer(), nullable=False),
        sa.Column("tournament_name", sa.String(length=255), nullable=True),
        sa.Column("tournament_tier", sa.String(length=64), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("winner_team_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_a_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["team_b_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["winner_team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_matches_id"), "matches", ["id"], unique=False)
    op.create_index(op.f("ix_matches_start_time"), "matches", ["start_time"], unique=False)
    op.create_index(op.f("ix_matches_status"), "matches", ["status"], unique=False)
    op.create_index(op.f("ix_matches_team_a_id"), "matches", ["team_a_id"], unique=False)
    op.create_index(op.f("ix_matches_team_b_id"), "matches", ["team_b_id"], unique=False)

    op.create_table(
        "team_match_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("kills", sa.Integer(), nullable=True),
        sa.Column("deaths", sa.Integer(), nullable=True),
        sa.Column("assists", sa.Integer(), nullable=True),
        sa.Column("gold_diff_10", sa.Integer(), nullable=True),
        sa.Column("xp_diff_10", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_match_stats_id"), "team_match_stats", ["id"], unique=False)
    op.create_index(op.f("ix_team_match_stats_match_id"), "team_match_stats", ["match_id"], unique=False)
    op.create_index(op.f("ix_team_match_stats_team_id"), "team_match_stats", ["team_id"], unique=False)

    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_a_probability", sa.Float(), nullable=False),
        sa.Column("team_b_probability", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation_json", sa.JSON(), nullable=True),
        sa.Column("model_type", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_predictions_id"), "predictions", ["id"], unique=False)
    op.create_index(op.f("ix_predictions_match_id"), "predictions", ["match_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_predictions_match_id"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_id"), table_name="predictions")
    op.drop_table("predictions")
    op.drop_index(op.f("ix_team_match_stats_team_id"), table_name="team_match_stats")
    op.drop_index(op.f("ix_team_match_stats_match_id"), table_name="team_match_stats")
    op.drop_index(op.f("ix_team_match_stats_id"), table_name="team_match_stats")
    op.drop_table("team_match_stats")
    op.drop_index(op.f("ix_matches_team_b_id"), table_name="matches")
    op.drop_index(op.f("ix_matches_team_a_id"), table_name="matches")
    op.drop_index(op.f("ix_matches_status"), table_name="matches")
    op.drop_index(op.f("ix_matches_start_time"), table_name="matches")
    op.drop_index(op.f("ix_matches_id"), table_name="matches")
    op.drop_table("matches")
    op.drop_index(op.f("ix_players_team_id"), table_name="players")
    op.drop_index(op.f("ix_players_nickname"), table_name="players")
    op.drop_index(op.f("ix_players_id"), table_name="players")
    op.drop_table("players")
    op.drop_index(op.f("ix_teams_name"), table_name="teams")
    op.drop_index(op.f("ix_teams_id"), table_name="teams")
    op.drop_table("teams")
