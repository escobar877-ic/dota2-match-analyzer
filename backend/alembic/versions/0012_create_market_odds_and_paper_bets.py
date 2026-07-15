"""Create market odds snapshots and paper bets.

Revision ID: 0012_market_odds_paper_bets
Revises: 0011_upcoming_classification
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_market_odds_paper_bets"
down_revision = "0011_upcoming_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("bookmaker", sa.String(length=128), nullable=False),
        sa.Column("market_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("decimal_odds", sa.Float(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_odds_match_id", "market_odds_snapshots", ["match_id"])
    op.create_index("ix_market_odds_captured_at", "market_odds_snapshots", ["captured_at"])

    op.create_table(
        "paper_bets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("market_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("model_probability", sa.Float(), nullable=False),
        sa.Column("decimal_odds", sa.Float(), nullable=False),
        sa.Column("no_vig_probability", sa.Float(), nullable=False),
        sa.Column("edge", sa.Float(), nullable=False),
        sa.Column("expected_value", sa.Float(), nullable=False),
        sa.Column("stake_units", sa.Float(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("profit_units", sa.Float(), nullable=True),
        sa.Column("guard_reasons_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_paper_bets_match_id", "paper_bets", ["match_id"])
    op.create_index("ix_paper_bets_status", "paper_bets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_paper_bets_status", table_name="paper_bets")
    op.drop_index("ix_paper_bets_match_id", table_name="paper_bets")
    op.drop_table("paper_bets")
    op.drop_index("ix_market_odds_captured_at", table_name="market_odds_snapshots")
    op.drop_index("ix_market_odds_match_id", table_name="market_odds_snapshots")
    op.drop_table("market_odds_snapshots")
