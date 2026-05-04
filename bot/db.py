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
                "sect_last_settlement_on" not in character_columns,
                "sect_last_settlement_summary" not in character_columns,
                "sect_task_refresh_on" not in character_columns,
                "sect_task_state_json" not in character_columns,
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
                # 证道战场
                "pg_boss_kills_json" not in character_columns,
                "pg_total_score" not in character_columns,
                "pg_best_score" not in character_columns,
                "pg_completions" not in character_columns,
                "pg_red_dust_count" not in character_columns,
                "dao_traces" not in character_columns,
                "proving_ground_runs" not in table_names,
                # 证道战场 -- 局外投资
                "pg_invest_stat_level" not in character_columns,
                "pg_invest_affix_slots" not in character_columns,
                "pg_invest_spirit_unlocked" not in character_columns,
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
            needs_sect_last_settlement_on,
            needs_sect_last_settlement_summary,
            needs_sect_task_refresh_on,
            needs_sect_task_state_json,
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
            # 证道战场
            needs_pg_boss_kills_json,
            needs_pg_total_score,
            needs_pg_best_score,
            needs_pg_completions,
            needs_pg_red_dust_count,
            needs_dao_traces,
            needs_proving_ground_runs_table,
            # 证道战场 -- 局外投资
            needs_pg_invest_stat_level,
            needs_pg_invest_affix_slots,
            needs_pg_invest_spirit_unlocked,
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
        if needs_sect_last_settlement_on:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_last_settlement_on DATE"))
        if needs_sect_last_settlement_summary:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_last_settlement_summary TEXT NOT NULL DEFAULT ''"))
        if needs_sect_task_refresh_on:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_task_refresh_on DATE"))
        if needs_sect_task_state_json:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN sect_task_state_json TEXT NOT NULL DEFAULT ''"))
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
        # 证道战场
        if needs_pg_boss_kills_json:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_boss_kills_json TEXT NOT NULL DEFAULT '[]'"))
        if needs_pg_total_score:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_total_score BIGINT NOT NULL DEFAULT 0"))
        if needs_pg_best_score:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_best_score INTEGER NOT NULL DEFAULT 0"))
        if needs_pg_completions:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_completions INTEGER NOT NULL DEFAULT 0"))
        if needs_pg_red_dust_count:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_red_dust_count INTEGER NOT NULL DEFAULT 0"))
        if needs_dao_traces:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN dao_traces BIGINT NOT NULL DEFAULT 0"))
        if needs_proving_ground_runs_table:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS proving_ground_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    status VARCHAR NOT NULL DEFAULT 'running',
                    map_json TEXT NOT NULL DEFAULT '{}',
                    current_node_id INTEGER NOT NULL DEFAULT 0,
                    build_json TEXT NOT NULL DEFAULT '{}',
                    pending_affix_ops INTEGER NOT NULL DEFAULT 0,
                    pending_spirit_ops INTEGER NOT NULL DEFAULT 0,
                    boss_type VARCHAR NOT NULL DEFAULT '',
                    boss_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    score INTEGER NOT NULL DEFAULT 0,
                    lingshi_invested BIGINT NOT NULL DEFAULT 0,
                    last_action_at DATETIME,
                    created_at DATETIME DEFAULT (datetime('now')),
                    updated_at DATETIME DEFAULT (datetime('now'))
                )
            """))
            await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_proving_ground_runs_character_id ON proving_ground_runs(character_id)"))
        # 证道战场 -- 局外投资
        if needs_pg_invest_stat_level:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_invest_stat_level INTEGER NOT NULL DEFAULT 0"))
        if needs_pg_invest_affix_slots:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_invest_affix_slots INTEGER NOT NULL DEFAULT 0"))
        if needs_pg_invest_spirit_unlocked:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN pg_invest_spirit_unlocked BOOLEAN NOT NULL DEFAULT 0"))


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_schema_compatibility(engine)
