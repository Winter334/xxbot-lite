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
        def _needs_retreat_column(sync_connection) -> bool:
            inspector = inspect(sync_connection)
            table_names = inspector.get_table_names()
            if "characters" not in table_names:
                return False
            columns = {column["name"] for column in inspector.get_columns("characters")}
            return "is_retreating" not in columns

        needs_retreat_column = await connection.run_sync(_needs_retreat_column)
        if needs_retreat_column:
            await connection.execute(text("ALTER TABLE characters ADD COLUMN is_retreating BOOLEAN NOT NULL DEFAULT 0"))


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_schema_compatibility(engine)
