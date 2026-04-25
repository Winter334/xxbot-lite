from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.models.base import Base


def _prepare_sqlite_path(database_url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return
    relative_path = database_url.removeprefix(prefix)
    if relative_path == ":memory:":
        return
    Path(relative_path).parent.mkdir(parents=True, exist_ok=True)


def create_engine_and_session_factory(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    _prepare_sqlite_path(database_url)
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def ensure_schema_compatibility(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        def _collect_missing_columns(sync_connection) -> tuple[bool, ...]:
            inspector = inspect(sync_connection)
            table_names = inspector.get_table_names()
            character_columns = {column["name"] for column in inspector.get_columns("characters")} if "characters" in table_names else set()
            artifact_columns = {column["name"] for column in inspector.get_columns("artifacts")} if "artifacts" in table_names else set()
            resource_site_columns = {column["name"] for column in inspector.get_columns("world_resource_sites")} if "world_resource_sites" in table_names else set()
            return (
                "is_retreating" not in character_columns,
                "retreat_mode" not in character_columns,
                "is_traveling" not in character_columns,
                "travel_started_at" not in character_columns,
                "travel_duration_minutes" not in character_columns,
                "travel_selected_duration_minutes" not in character_columns,
                "travel_atk_pct" not in character_columns,
                "travel_def_pct" not in character_columns,
                "travel_agi_pct" not in character_columns,
                "faction" not in character_columns,
                "virtue" not in character_columns,
                "infamy" not in character_columns,
                "luck" not in character_columns,
                "bounty_soul" not in character_columns,
                "last_bounty_growth_on" not in character_columns,
                "last_robbery_at" not in character_columns,
                "last_bounty_hunt_at" not in character_columns,
                "last_bounty_defeated_on" not in character_columns,
                "sect_id" not in character_columns,
                "sect_joined_at" not in character_columns,
                "sect_last_left_at" not in character_columns,
                "sect_contribution_total" not in character_columns,
                "sect_contribution_weekly" not in character_columns,
                "sect_contribution_daily" not in character_columns,
                "sect_last_contribution_on" not in character_columns,
                "sect_bound_site_id" not in character_columns,
                "sect_bound_site_role" not in character_columns,
                "lingshi" not in character_columns,
                "honor_tags_json" not in character_columns,
                "spawned_on" not in resource_site_columns,
                "expires_on" not in resource_site_columns,
                "affix_slots_json" not in artifact_columns,
                "affix_pending_json" not in artifact_columns,
                "spirit_name" not in artifact_columns,
                "spirit_rename_used" not in artifact_columns,
                "spirit_json" not in artifact_columns,
                "spirit_pending_json" not in artifact_columns,
                "spirit_refining_until" not in artifact_columns,
                "spirit_refining_mode" not in artifact_columns,
            )

        (
            needs_retreat_column,
            needs_retreat_mode,
            needs_traveling_column,
            needs_travel_started_at,
            needs_travel_duration,
            needs_travel_selected_duration,
            needs_travel_atk_pct,
            needs_travel_def_pct,
            needs_travel_agi_pct,
            needs_faction,
            needs_virtue,
            needs_infamy,
            needs_luck,
            needs_bounty_soul,
            needs_last_bounty_growth_on,
            needs_last_robbery_at,
            needs_last_bounty_hunt_at,
            needs_last_bounty_defeated_on,
            needs_sect_id,
            needs_sect_joined_at,
            needs_sect_last_left_at,
            needs_sect_contribution_total,
            needs_sect_contribution_weekly,
            needs_sect_contribution_daily,
            needs_sect_last_contribution_on,
            needs_sect_bound_site_id,
            needs_sect_bound_site_role,
            needs_lingshi,
            needs_honor_tags_json,
            needs_site_spawned_on,
            needs_site_expires_on,
            needs_affix_slots,
            needs_affix_pending,
            needs_spirit_name,
            needs_spirit_rename_used,
            needs_spirit_json,
            needs_spirit_pending,
            needs_spirit_refining_until,
            needs_spirit_refining_mode,
        ) = await connection.run_sync(_collect_missing_columns)
        if needs_retreat_column:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN is_retreating BOOLEAN NOT NULL DEFAULT 0"))
        if needs_retreat_mode:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN retreat_mode VARCHAR(16) NOT NULL DEFAULT 'cultivation'"))
        if needs_traveling_column:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN is_traveling BOOLEAN NOT NULL DEFAULT 0"))
        if needs_travel_started_at:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_started_at DATETIME"))
            await connection.execute(text("UPDATE characters SET travel_started_at = COALESCE(travel_started_at, last_idle_at)"))
        if needs_travel_duration:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_duration_minutes INTEGER NOT NULL DEFAULT 0"))
        if needs_travel_selected_duration:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_selected_duration_minutes INTEGER NOT NULL DEFAULT 120"))
        if needs_travel_atk_pct:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_atk_pct INTEGER NOT NULL DEFAULT 0"))
        if needs_travel_def_pct:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_def_pct INTEGER NOT NULL DEFAULT 0"))
        if needs_travel_agi_pct:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN travel_agi_pct INTEGER NOT NULL DEFAULT 0"))
        if needs_faction:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN faction VARCHAR(16) NOT NULL DEFAULT 'neutral'"))
        if needs_virtue:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN virtue BIGINT NOT NULL DEFAULT 0"))
        if needs_infamy:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN infamy BIGINT NOT NULL DEFAULT 0"))
        if needs_luck:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN luck BIGINT NOT NULL DEFAULT 0"))
        if needs_bounty_soul:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN bounty_soul BIGINT NOT NULL DEFAULT 0"))
        if needs_last_bounty_growth_on:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN last_bounty_growth_on DATE"))
        if needs_last_robbery_at:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN last_robbery_at DATETIME"))
        if needs_last_bounty_hunt_at:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN last_bounty_hunt_at DATETIME"))
        if needs_last_bounty_defeated_on:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN last_bounty_defeated_on DATE"))
        if needs_sect_id:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_id INTEGER"))
        if needs_sect_joined_at:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_joined_at DATETIME"))
        if needs_sect_last_left_at:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_last_left_at DATETIME"))
        if needs_sect_contribution_total:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_contribution_total BIGINT NOT NULL DEFAULT 0"))
        if needs_sect_contribution_weekly:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_contribution_weekly BIGINT NOT NULL DEFAULT 0"))
        if needs_sect_contribution_daily:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_contribution_daily BIGINT NOT NULL DEFAULT 0"))
        if needs_sect_last_contribution_on:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_last_contribution_on DATE"))
        if needs_sect_bound_site_id:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_bound_site_id INTEGER"))
        if needs_sect_bound_site_role:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_bound_site_role VARCHAR(16)"))
        if needs_lingshi:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN lingshi BIGINT NOT NULL DEFAULT 0"))
        if needs_honor_tags_json:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN honor_tags_json TEXT NOT NULL DEFAULT '[]'"))
        if needs_site_spawned_on:
            await connection.execute(text("ALTER TABLE world_resource_sites ADD COLUMN spawned_on DATE"))
            await connection.execute(text("UPDATE world_resource_sites SET spawned_on = COALESCE(spawned_on, settlement_day, DATE('now'))"))
        if needs_site_expires_on:
            await connection.execute(text("ALTER TABLE world_resource_sites ADD COLUMN expires_on DATE"))
            await connection.execute(text("UPDATE world_resource_sites SET expires_on = COALESCE(expires_on, DATE(COALESCE(spawned_on, settlement_day, DATE('now')), '+2 day'))"))
        if needs_affix_slots:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN affix_slots_json TEXT NOT NULL DEFAULT '[]'"))
        if needs_affix_pending:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN affix_pending_json TEXT NOT NULL DEFAULT '[]'"))
        if needs_spirit_name:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_name VARCHAR(64)"))
        if needs_spirit_rename_used:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_rename_used BOOLEAN NOT NULL DEFAULT 0"))
        if needs_spirit_json:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_json TEXT NOT NULL DEFAULT ''"))
        if needs_spirit_pending:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_pending_json TEXT NOT NULL DEFAULT ''"))
        if needs_spirit_refining_until:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_refining_until DATETIME"))
        if needs_spirit_refining_mode:
            await connection.execute(text("ALTER TABLE artifacts ADD COLUMN spirit_refining_mode VARCHAR(16)"))


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_schema_compatibility(engine)
