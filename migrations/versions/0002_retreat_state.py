"""add retreat state to characters

Revision ID: 0002_retreat_state
Revises: 0001_initial
Create Date: 2026-04-06 12:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_retreat_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("characters")}
    if "is_retreating" not in columns:
        op.add_column(
            "characters",
            sa.Column("is_retreating", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("characters")}
    if "is_retreating" in columns:
        op.drop_column("characters", "is_retreating")
