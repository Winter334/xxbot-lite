"""add selected travel duration to characters

Revision ID: 0003_travel_selected_duration
Revises: 0002_retreat_state
Create Date: 2026-04-06 17:58:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_travel_selected_duration"
down_revision = "0002_retreat_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("characters")}
    if "travel_selected_duration_minutes" not in columns:
        op.add_column(
            "characters",
            sa.Column("travel_selected_duration_minutes", sa.Integer(), nullable=False, server_default=sa.text("120")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("characters")}
    if "travel_selected_duration_minutes" in columns:
        op.drop_column("characters", "travel_selected_duration_minutes")
