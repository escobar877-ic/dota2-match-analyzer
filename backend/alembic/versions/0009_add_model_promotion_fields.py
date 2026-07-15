"""add model promotion fields

Revision ID: 0009_add_model_promotion_fields
Revises: 0008_create_roster_patch_tables
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_add_model_promotion_fields"
down_revision = "0008_create_roster_patch_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_versions", sa.Column("status", sa.String(length=32), nullable=False, server_default="candidate"))
    op.add_column("model_versions", sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("promotion_reason", sa.Text(), nullable=True))
    op.add_column("model_versions", sa.Column("artifact_metadata_json", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_model_versions_status"), "model_versions", ["status"], unique=False)
    op.execute("UPDATE model_versions SET status = 'active' WHERE is_active = true")


def downgrade() -> None:
    op.drop_index(op.f("ix_model_versions_status"), table_name="model_versions")
    op.drop_column("model_versions", "artifact_metadata_json")
    op.drop_column("model_versions", "promotion_reason")
    op.drop_column("model_versions", "rejected_at")
    op.drop_column("model_versions", "promoted_at")
    op.drop_column("model_versions", "status")
