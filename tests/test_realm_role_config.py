from __future__ import annotations

from importlib import import_module
import json
from pathlib import Path
from types import ModuleType

import pytest

from bot.data.realms import REALM_STAGES


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    # Importing config loads .env, so isolate it from the real workspace first.
    monkeypatch.chdir(tmp_path)
    for name in (
        "DISCORD_TOKEN",
        "APPLICATION_ID",
        "DATABASE_URL",
        "BROADCAST_CHANNEL_ID",
        "LOG_LEVEL",
        "REALM_ROLE_IDS",
        "REALM_ROLE_CLEANUP_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    return import_module("bot.config")


def test_settings_preserve_existing_constructors_and_independent_defaults(config) -> None:
    first = config.Settings("test-token", None, "sqlite+aiosqlite:///:memory:", None)
    second = config.Settings("test-token", None, "sqlite+aiosqlite:///:memory:", None, "DEBUG")

    assert first.log_level == "INFO"
    assert second.log_level == "DEBUG"
    assert first.realm_role_ids == second.realm_role_ids == {}
    assert first.realm_role_ids is not second.realm_role_ids
    assert first.realm_role_cleanup_ids == second.realm_role_cleanup_ids == frozenset()
    assert isinstance(first.realm_role_cleanup_ids, frozenset)
    first.realm_role_ids["lianqi"] = 123
    assert second.realm_role_ids == {}


@pytest.mark.parametrize("raw_value", [None, "", " \t\n ", "{}", " \n {} \t"])
def test_empty_realm_role_config(config, monkeypatch, raw_value) -> None:
    if raw_value is not None:
        monkeypatch.setenv("REALM_ROLE_IDS", raw_value)

    assert config.load_settings().realm_role_ids == {}


@pytest.mark.parametrize("raw_value", [None, "", " \t\n ", "[]", " \n [] \t"])
def test_empty_realm_role_cleanup_config(config, monkeypatch, raw_value) -> None:
    if raw_value is not None:
        monkeypatch.setenv("REALM_ROLE_CLEANUP_IDS", raw_value)

    cleanup_ids = config.load_settings().realm_role_cleanup_ids

    assert cleanup_ids == frozenset()
    assert isinstance(cleanup_ids, frozenset)


def test_cleanup_ids_normalize_and_deduplicate_mixed_id_types(config, monkeypatch) -> None:
    monkeypatch.setenv("REALM_ROLE_CLEANUP_IDS", '[1, "1", "00001", 10001, "10001", "00010001"]')

    cleanup_ids = config.load_settings().realm_role_cleanup_ids

    assert cleanup_ids == frozenset({1, 10001})
    assert isinstance(cleanup_ids, frozenset)
    assert all(type(role_id) is int for role_id in cleanup_ids)


@pytest.mark.parametrize(
    "raw_value",
    ["[", "[123,]", "[0x10]", "{}", '{"lianqi":123}', "null", "123", '"123"', "true", "1.5"],
)
def test_cleanup_config_rejects_invalid_json_and_non_arrays(config, monkeypatch, raw_value) -> None:
    monkeypatch.setenv("REALM_ROLE_CLEANUP_IDS", raw_value)

    with pytest.raises(ValueError, match="REALM_ROLE_CLEANUP_IDS"):
        config.load_settings()


@pytest.mark.parametrize("role_id", [1, 10001, "1", "10001", "00010001"])
def test_role_ids_accept_positive_integers_and_decimal_strings(config, monkeypatch, role_id) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", json.dumps({"lianqi": role_id}))

    role_ids = config.load_settings().realm_role_ids

    assert role_ids == {"lianqi": int(role_id)}
    assert type(role_ids["lianqi"]) is int


def test_all_open_realms_accept_mixed_id_types(config, monkeypatch) -> None:
    expected = {stage.realm_key: stage.realm_index for stage in REALM_STAGES}
    mapping = {key: str(value) if value % 2 else value for key, value in expected.items()}
    monkeypatch.setenv("REALM_ROLE_IDS", json.dumps(mapping))

    role_ids = config.load_settings().realm_role_ids

    assert role_ids == expected
    assert all(type(value) is int for value in role_ids.values())


def test_loading_settings_uses_fresh_role_mappings(config, monkeypatch) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", '{"lianqi":123}')
    monkeypatch.setenv("REALM_ROLE_CLEANUP_IDS", '[123, "456"]')
    first = config.load_settings()
    second = config.load_settings()

    assert first.realm_role_ids is not second.realm_role_ids
    first.realm_role_ids["lianqi"] = 456
    assert second.realm_role_ids == {"lianqi": 123}
    assert first.realm_role_cleanup_ids == second.realm_role_cleanup_ids == frozenset({123, 456})

    monkeypatch.setenv("REALM_ROLE_IDS", '{"weixian":"789"}')
    monkeypatch.setenv("REALM_ROLE_CLEANUP_IDS", '["789"]')
    latest = config.load_settings()
    assert latest.realm_role_ids == {"weixian": 789}
    assert latest.realm_role_cleanup_ids == frozenset({789})
    assert second.realm_role_cleanup_ids == frozenset({123, 456})


def test_realm_role_config_preserves_other_settings(config, monkeypatch) -> None:
    for key, value in {
        "DISCORD_TOKEN": "test-token",
        "APPLICATION_ID": "123",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "BROADCAST_CHANNEL_ID": "456",
        "LOG_LEVEL": "debug",
        "REALM_ROLE_IDS": '{"lianqi":"789"}',
        "REALM_ROLE_CLEANUP_IDS": '[789, "01234", 1234]',
    }.items():
        monkeypatch.setenv(key, value)

    settings = config.load_settings()

    assert settings.discord_token == "test-token"
    assert settings.application_id == 123
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.broadcast_channel_id == 456
    assert settings.broadcast_enabled
    assert settings.log_level == "DEBUG"
    assert settings.realm_role_ids == {"lianqi": 789}
    assert settings.realm_role_cleanup_ids == frozenset({789, 1234})


@pytest.mark.parametrize("raw_value", ["{", "{'lianqi':123}", '{"lianqi":123,}', '{"lianqi":0x10}'])
def test_malformed_json_is_rejected(config, monkeypatch, raw_value) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", raw_value)

    with pytest.raises(ValueError, match="REALM_ROLE_IDS.*JSON object"):
        config.load_settings()


@pytest.mark.parametrize("value", [None, [], [["lianqi", 123]], 123, "123", True, False, 1.5])
def test_non_object_json_is_rejected(config, monkeypatch, value) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", json.dumps(value))

    with pytest.raises(ValueError, match="REALM_ROLE_IDS.*JSON object"):
        config.load_settings()


@pytest.mark.parametrize("env_name", ["REALM_ROLE_IDS", "REALM_ROLE_CLEANUP_IDS"])
@pytest.mark.parametrize(
    "role_id",
    [
        True,
        False,
        0,
        -1,
        1.0,
        1.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        [],
        {},
        "",
        "0",
        "000",
        "-1",
        "+1",
        "1.0",
        "1e3",
        "0x10",
        "0o10",
        "0b10",
        "1_000",
        "1,000",
        "abc",
        " 123",
        "123 ",
        "123\n",
        "\u00b2",
        "\uff11",
        "\u0661",
    ],
)
def test_invalid_role_id_values_are_rejected(config, monkeypatch, role_id, env_name) -> None:
    value = {"lianqi": role_id} if env_name == "REALM_ROLE_IDS" else [123, role_id]
    monkeypatch.setenv(env_name, json.dumps(value))
    message = "REALM_ROLE_IDS.*lianqi.*positive integer" if env_name == "REALM_ROLE_IDS" else env_name

    with pytest.raises(ValueError, match=message):
        config.load_settings()


@pytest.mark.parametrize("first_id, second_id", [(123, 123), (123, "123"), ("123", 123), ("00123", "123")])
def test_duplicate_ids_are_rejected_after_normalization(config, monkeypatch, first_id, second_id) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", json.dumps({"lianqi": first_id, "weixian": second_id}))

    with pytest.raises(ValueError, match="REALM_ROLE_IDS.*duplicate role ID 123.*lianqi.*weixian"):
        config.load_settings()


@pytest.mark.parametrize(
    "realm_key",
    ["unknown", "", "zhenxian", "tianxian", "jinxian", "Lianqi", "lianqi.early", "early", "\u70bc\u6c14"],
)
def test_unknown_and_unopened_realms_are_rejected(config, monkeypatch, realm_key) -> None:
    monkeypatch.setenv("REALM_ROLE_IDS", json.dumps({realm_key: 123}))

    with pytest.raises(ValueError, match="REALM_ROLE_IDS.*unknown realm_key"):
        config.load_settings()
