"""add artifact affix storage

Revision ID: 0003_artifact_affixes
Revises: 0002_retreat_state
Create Date: 2026-04-06 16:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_artifact_affixes"
down_revision = "0002_retreat_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("artifacts")}
    if "affix_slots_json" not in columns:
        op.add_column(
            "artifacts",
            sa.Column("affix_slots_json", sa.Text(), nullable=False, server_default="[]"),
        )
    if "affix_pending_json" not in columns:
        op.add_column(
            "artifacts",
            sa.Column("affix_pending_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("artifacts")}
    if "affix_pending_json" in columns:
        op.drop_column("artifacts", "affix_pending_json")
    if "affix_slots_json" in columns:
        op.drop_column("artifacts", "affix_slots_json")
