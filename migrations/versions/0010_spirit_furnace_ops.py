"""spirit long furnace columns (spirit_furnace_started_at / spirit_ops / spirit_choices_json)

Revision ID: 0010_spirit_furnace_ops
Revises: 0009_clamp_duofeng
Create Date: 2026-09-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_spirit_furnace_ops"
down_revision = "0009_clamp_duofeng"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()  # noqa: F821
    inspector = sa.inspect(bind)  # noqa: F821
    if "artifacts" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("artifacts")}
    if "spirit_furnace_started_at" not in columns:
        op.add_column("artifacts", sa.Column("spirit_furnace_started_at", sa.DateTime(timezone=True), nullable=True))
    if "spirit_ops" not in columns:
        op.add_column("artifacts", sa.Column("spirit_ops", sa.Integer(), nullable=False, server_default=sa.text("0")))
    if "spirit_choices_json" not in columns:
        op.add_column("artifacts", sa.Column("spirit_choices_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    bind = op.get_bind()  # noqa: F821
    inspector = sa.inspect(bind)  # noqa: F821
    if "artifacts" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("artifacts")}
    if "spirit_choices_json" in columns:
        op.drop_column("artifacts", "spirit_choices_json")
    if "spirit_ops" in columns:
        op.drop_column("artifacts", "spirit_ops")
    if "spirit_furnace_started_at" in columns:
        op.drop_column("artifacts", "spirit_furnace_started_at")
