"""add historical_max_infamy to characters

Revision ID: 0008_historical_max_infamy
Revises: 0007_add_npc_fields
Create Date: 2026-05-13 18:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_historical_max_infamy"
down_revision = "0007_add_npc_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "historical_max_infamy" not in character_columns:
            op.add_column(
                "characters",
                sa.Column(
                    "historical_max_infamy",
                    sa.BigInteger(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )
            # 把现有 infamy 当成历史最大值回填（避免初次升级丢失阶梯）
            op.execute(
                "UPDATE characters SET historical_max_infamy = infamy WHERE infamy > historical_max_infamy"
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "historical_max_infamy" in character_columns:
            op.drop_column("characters", "historical_max_infamy")
