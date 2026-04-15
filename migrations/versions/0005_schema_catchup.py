"""add remaining schema columns covered by runtime compatibility

Revision ID: 0005_schema_catchup
Revises: 0004_merge_parallel_heads
Create Date: 2026-04-15 16:46:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_schema_catchup"
down_revision = "0004_merge_parallel_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "is_traveling" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("is_traveling", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            )
        if "travel_started_at" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("travel_started_at", sa.DateTime(timezone=True), nullable=True),
            )
            op.execute(sa.text("UPDATE characters SET travel_started_at = COALESCE(travel_started_at, last_idle_at)"))
        if "travel_duration_minutes" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("travel_duration_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "travel_atk_pct" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("travel_atk_pct", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "travel_def_pct" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("travel_def_pct", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "travel_agi_pct" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("travel_agi_pct", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "faction" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("faction", sa.String(length=16), nullable=False, server_default=sa.text("'neutral'")),
            )
        if "virtue" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("virtue", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            )
        if "infamy" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("infamy", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            )
        if "luck" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("luck", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            )
        if "bounty_soul" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("bounty_soul", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            )
        if "last_bounty_growth_on" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("last_bounty_growth_on", sa.Date(), nullable=True),
            )
        if "last_robbery_at" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("last_robbery_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "last_bounty_hunt_at" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("last_bounty_hunt_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "last_bounty_defeated_on" not in character_columns:
            op.add_column(
                "characters",
                sa.Column("last_bounty_defeated_on", sa.Date(), nullable=True),
            )

    if "artifacts" in table_names:
        artifact_columns = {column["name"] for column in inspector.get_columns("artifacts")}
        if "spirit_name" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_name", sa.String(length=64), nullable=True),
            )
        if "spirit_rename_used" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_rename_used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            )
        if "spirit_json" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_json", sa.Text(), nullable=False, server_default=sa.text("''")),
            )
        if "spirit_pending_json" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_pending_json", sa.Text(), nullable=False, server_default=sa.text("''")),
            )
        if "spirit_refining_until" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_refining_until", sa.DateTime(timezone=True), nullable=True),
            )
        if "spirit_refining_mode" not in artifact_columns:
            op.add_column(
                "artifacts",
                sa.Column("spirit_refining_mode", sa.String(length=16), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "artifacts" in table_names:
        artifact_columns = {column["name"] for column in inspector.get_columns("artifacts")}
        if "spirit_refining_mode" in artifact_columns:
            op.drop_column("artifacts", "spirit_refining_mode")
        if "spirit_refining_until" in artifact_columns:
            op.drop_column("artifacts", "spirit_refining_until")
        if "spirit_pending_json" in artifact_columns:
            op.drop_column("artifacts", "spirit_pending_json")
        if "spirit_json" in artifact_columns:
            op.drop_column("artifacts", "spirit_json")
        if "spirit_rename_used" in artifact_columns:
            op.drop_column("artifacts", "spirit_rename_used")
        if "spirit_name" in artifact_columns:
            op.drop_column("artifacts", "spirit_name")

    if "characters" in table_names:
        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        if "last_bounty_defeated_on" in character_columns:
            op.drop_column("characters", "last_bounty_defeated_on")
        if "last_bounty_hunt_at" in character_columns:
            op.drop_column("characters", "last_bounty_hunt_at")
        if "last_robbery_at" in character_columns:
            op.drop_column("characters", "last_robbery_at")
        if "last_bounty_growth_on" in character_columns:
            op.drop_column("characters", "last_bounty_growth_on")
        if "bounty_soul" in character_columns:
            op.drop_column("characters", "bounty_soul")
        if "luck" in character_columns:
            op.drop_column("characters", "luck")
        if "infamy" in character_columns:
            op.drop_column("characters", "infamy")
        if "virtue" in character_columns:
            op.drop_column("characters", "virtue")
        if "faction" in character_columns:
            op.drop_column("characters", "faction")
        if "travel_agi_pct" in character_columns:
            op.drop_column("characters", "travel_agi_pct")
        if "travel_def_pct" in character_columns:
            op.drop_column("characters", "travel_def_pct")
        if "travel_atk_pct" in character_columns:
            op.drop_column("characters", "travel_atk_pct")
        if "travel_duration_minutes" in character_columns:
            op.drop_column("characters", "travel_duration_minutes")
        if "travel_started_at" in character_columns:
            op.drop_column("characters", "travel_started_at")
        if "is_traveling" in character_columns:
            op.drop_column("characters", "is_traveling")
