"""add NPC fields to characters

Revision ID: 0007_add_npc_fields
Revises: 0006_travel_honor_tags
Create Date: 2026-05-08 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_add_npc_fields"
down_revision = "0006_travel_honor_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "is_npc" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("is_npc", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            )
            op.create_index("ix_characters_is_npc", "characters", ["is_npc"], unique=False)
        if "npc_spawned_on" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("npc_spawned_on", sa.Date(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "npc_spawned_on" in character_columns:
            op.drop_column("characters", "npc_spawned_on")
        if "is_npc" in character_columns:
            try:
                op.drop_index("ix_characters_is_npc", table_name="characters")
            except Exception:
                pass
            op.drop_column("characters", "is_npc")
