"""create backtests

Revision ID: 0006_create_backtests
Revises: 0005_create_model_versions
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_create_backtests"
down_revision = "0005_create_model_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("matches_count", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("report_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_backtests_id"), "backtests", ["id"], unique=False)
    op.create_index(op.f("ix_backtests_model_version_id"), "backtests", ["model_version_id"], unique=False)
    op.create_index(op.f("ix_backtests_started_at"), "backtests", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_backtests_started_at"), table_name="backtests")
    op.drop_index(op.f("ix_backtests_model_version_id"), table_name="backtests")
    op.drop_index(op.f("ix_backtests_id"), table_name="backtests")
    op.drop_table("backtests")
