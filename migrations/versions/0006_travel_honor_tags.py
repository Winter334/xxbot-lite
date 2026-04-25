"""add persistent character honor tags

Revision ID: 0006_travel_honor_tags
Revises: 0005_schema_catchup
Create Date: 2026-04-25 15:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_travel_honor_tags"
down_revision = "0005_schema_catchup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "honor_tags_json" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("honor_tags_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "honor_tags_json" in character_columns:
            op.drop_column("characters", "honor_tags_json")
