"""initial tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-04 16:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("discord_user_id", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_players_discord_user_id"), "players", ["discord_user_id"], unique=True)

    op.create_table(
        "characters",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("realm_key", sa.String(length=16), nullable=False),
        sa.Column("realm_index", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(length=16), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("cultivation", sa.BigInteger(), nullable=False),
        sa.Column("highest_floor", sa.Integer(), nullable=False),
        sa.Column("historical_highest_floor", sa.Integer(), nullable=False),
        sa.Column("current_qi", sa.Integer(), nullable=False),
        sa.Column("qi_max", sa.Integer(), nullable=False),
        sa.Column("last_idle_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_qi_recovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fate_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("combat_power", sa.BigInteger(), nullable=False),
        sa.Column("best_ladder_rank", sa.Integer(), nullable=False),
        sa.Column("current_ladder_rank", sa.Integer(), nullable=False),
        sa.Column("daily_pvp_attempts_used", sa.Integer(), nullable=False),
        sa.Column("last_pvp_reset_on", sa.Date(), nullable=True),
        sa.Column("reincarnation_count", sa.Integer(), nullable=False),
        sa.Column("last_reincarnated_on", sa.Date(), nullable=True),
        sa.Column("last_highlight_text", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id"),
    )
    op.create_index(op.f("ix_characters_current_ladder_rank"), "characters", ["current_ladder_rank"], unique=False)
    op.create_index(op.f("ix_characters_player_id"), "characters", ["player_id"], unique=True)

    op.create_table(
        "artifacts",
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("artifact_rename_used", sa.Boolean(), nullable=False),
        sa.Column("reinforce_level", sa.Integer(), nullable=False),
        sa.Column("atk_bonus", sa.BigInteger(), nullable=False),
        sa.Column("def_bonus", sa.BigInteger(), nullable=False),
        sa.Column("agi_bonus", sa.BigInteger(), nullable=False),
        sa.Column("soul_shards", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id"),
    )
    op.create_index(op.f("ix_artifacts_character_id"), "artifacts", ["character_id"], unique=True)

    op.create_table(
        "ladder_records",
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id"),
    )
    op.create_index(op.f("ix_ladder_records_character_id"), "ladder_records", ["character_id"], unique=True)
    op.create_index(op.f("ix_ladder_records_rank"), "ladder_records", ["rank"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ladder_records_rank"), table_name="ladder_records")
    op.drop_index(op.f("ix_ladder_records_character_id"), table_name="ladder_records")
    op.drop_table("ladder_records")
    op.drop_index(op.f("ix_artifacts_character_id"), table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_characters_player_id"), table_name="characters")
    op.drop_index(op.f("ix_characters_current_ladder_rank"), table_name="characters")
    op.drop_table("characters")
    op.drop_index(op.f("ix_players_discord_user_id"), table_name="players")
    op.drop_table("players")
