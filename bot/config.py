from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from bot.data.realms import REALM_STAGES


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
    realm_role_ids: dict[str, int] = field(default_factory=dict)
    realm_role_cleanup_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def broadcast_enabled(self) -> bool:
        return self.broadcast_channel_id is not None


def _parse_role_id(value: object, setting_name: str) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError(f"{setting_name} has an invalid role ID") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"{setting_name} must be a positive integer "
            "or a decimal digit string"
        )
    return value


def _load_realm_role_ids() -> dict[str, int]:
    raw_value = os.getenv("REALM_ROLE_IDS", "").strip()
    if not raw_value:
        return {}
    try:
        mapping = json.loads(raw_value)
    except ValueError as exc:
        raise ValueError("REALM_ROLE_IDS must be a valid JSON object") from exc
    if not isinstance(mapping, dict):
        raise ValueError("REALM_ROLE_IDS must be a JSON object")

    realm_keys = {stage.realm_key for stage in REALM_STAGES}
    role_ids: dict[str, int] = {}
    assigned_roles: dict[int, str] = {}
    for realm_key, role_id in mapping.items():
        if realm_key not in realm_keys:
            raise ValueError(f"REALM_ROLE_IDS contains unknown realm_key: {realm_key!r}")
        role_id = _parse_role_id(role_id, f"REALM_ROLE_IDS[{realm_key!r}]")
        if role_id in assigned_roles:
            raise ValueError(
                f"REALM_ROLE_IDS has duplicate role ID {role_id} for "
                f"{assigned_roles[role_id]!r} and {realm_key!r}"
            )
        role_ids[realm_key] = role_id
        assigned_roles[role_id] = realm_key
    return role_ids


def _load_realm_role_cleanup_ids() -> frozenset[int]:
    raw_value = os.getenv("REALM_ROLE_CLEANUP_IDS", "").strip()
    if not raw_value:
        return frozenset()
    try:
        values = json.loads(raw_value)
    except ValueError as exc:
        raise ValueError("REALM_ROLE_CLEANUP_IDS must be a valid JSON array") from exc
    if not isinstance(values, list):
        raise ValueError("REALM_ROLE_CLEANUP_IDS must be a JSON array")
    return frozenset(
        _parse_role_id(value, f"REALM_ROLE_CLEANUP_IDS[{index}]")
        for index, value in enumerate(values)
    )


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
        realm_role_ids=_load_realm_role_ids(),
        realm_role_cleanup_ids=_load_realm_role_cleanup_ids(),
    )
