"""create model versions

Revision ID: 0005_create_model_versions
Revises: 0004_tier1_cleanup_fields
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_create_model_versions"
down_revision = "0004_tier1_cleanup_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("train_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("train_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_versions_id"), "model_versions", ["id"], unique=False)
    op.create_index(op.f("ix_model_versions_is_active"), "model_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_model_versions_model_name"), "model_versions", ["model_name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_versions_model_name"), table_name="model_versions")
    op.drop_index(op.f("ix_model_versions_is_active"), table_name="model_versions")
    op.drop_index(op.f("ix_model_versions_id"), table_name="model_versions")
    op.drop_table("model_versions")
