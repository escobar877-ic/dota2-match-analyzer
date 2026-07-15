"""create data sync logs

Revision ID: 0007_create_data_sync_logs
Revises: 0006_create_backtests
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_create_data_sync_logs"
down_revision = "0006_create_backtests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_sync_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("sync_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_excluded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_data_sync_logs_id"), "data_sync_logs", ["id"], unique=False)
    op.create_index(op.f("ix_data_sync_logs_source"), "data_sync_logs", ["source"], unique=False)
    op.create_index(op.f("ix_data_sync_logs_started_at"), "data_sync_logs", ["started_at"], unique=False)
    op.create_index(op.f("ix_data_sync_logs_status"), "data_sync_logs", ["status"], unique=False)
    op.create_index(op.f("ix_data_sync_logs_sync_type"), "data_sync_logs", ["sync_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_data_sync_logs_sync_type"), table_name="data_sync_logs")
    op.drop_index(op.f("ix_data_sync_logs_status"), table_name="data_sync_logs")
    op.drop_index(op.f("ix_data_sync_logs_started_at"), table_name="data_sync_logs")
    op.drop_index(op.f("ix_data_sync_logs_source"), table_name="data_sync_logs")
    op.drop_index(op.f("ix_data_sync_logs_id"), table_name="data_sync_logs")
    op.drop_table("data_sync_logs")
