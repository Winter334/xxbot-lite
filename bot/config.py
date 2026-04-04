from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    application_id: int | None
    database_url: str
    broadcast_channel_id: int | None
    log_level: str = "INFO"

    @property
    def broadcast_enabled(self) -> bool:
        return self.broadcast_channel_id is not None


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "")
    application_id = os.getenv("APPLICATION_ID")
    broadcast_channel_id = os.getenv("BROADCAST_CHANNEL_ID")
    return Settings(
        discord_token=token,
        application_id=int(application_id) if application_id else None,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/xxbot.sqlite3"),
        broadcast_channel_id=int(broadcast_channel_id) if broadcast_channel_id else None,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
