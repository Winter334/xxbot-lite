from __future__ import annotations

from datetime import timedelta
import random

import pytest
from sqlalchemy import select, text

from bot.data.travel import TRAVEL_AGI_PCT_CAP, TRAVEL_ATK_PCT_CAP, TRAVEL_DEF_PCT_CAP
from bot.db import create_engine_and_session_factory, ensure_schema_compatibility, init_models
from bot.models import Base
from bot.models.character import Character
from bot.services.travel_service import TravelEventDefinition, TravelService
from bot.ui.panel import build_travel_embed, build_travel_settlement_embed
from bot.utils.time_utils import now_shanghai


def _bonuses(character) -> tuple[int, int, int]:
    return character.travel_atk_pct, character.travel_def_pct, character.travel_agi_pct


def _event(atk: int, defense: int, agility: int) -> TravelEventDefinition:
    return TravelEventDefinition(
        "test_stats",
        "Test stats",
        "Test event",
        1,
        atk_pct_min=atk,
        atk_pct_max=atk,
        def_pct_min=defense,
        def_pct_max=defense,
        agi_pct_min=agility,
        agi_pct_max=agility,
    )


def test_travel_bonus_caps() -> None:
    assert (TRAVEL_ATK_PCT_CAP, TRAVEL_DEF_PCT_CAP, TRAVEL_AGI_PCT_CAP) == (75, 75, 50)


@pytest.mark.parametrize(
    ("initial", "rolled", "expected", "actual"),
    [
        ((10, 20, 30), (2, 3, 4), (12, 23, 34), (2, 3, 4)),
        ((73, 72, 46), (2, 3, 4), (75, 75, 50), (2, 3, 4)),
        ((74, 74, 49), (2, 3, 4), (75, 75, 50), (1, 1, 1)),
        ((75, 75, 50), (2, 3, 4), (75, 75, 50), (0, 0, 0)),
        ((175, 175, 150), (2, 3, 4), (75, 75, 50), (0, 0, 0)),
        ((175, 175, 150), (-2, -3, -4), (73, 72, 46), (-2, -3, -4)),
        ((75, 75, 50), (2, -3, -4), (75, 72, 46), (0, -3, -4)),
        ((-10, -20, -30), (-2, -3, -4), (-12, -23, -34), (-2, -3, -4)),
        ((-10, -20, -30), (2, 3, 4), (-8, -17, -26), (2, 3, 4)),
        ((175, -20, 150), (0, 0, 0), (75, -20, 50), (0, 0, 0)),
    ],
)
def test_event_records_only_actual_bonus_changes(services, monkeypatch, initial, rolled, expected, actual) -> None:
    travel = TravelService(services.fate, random.Random(42))
    character = Character(
        travel_atk_pct=initial[0],
        travel_def_pct=initial[1],
        travel_agi_pct=initial[2],
    )
    monkeypatch.setattr(travel, "_roll_event", lambda: _event(*rolled))

    log = travel._resolve_event(character)

    assert _bonuses(character) == expected
    assert (log.atk_pct_delta, log.def_pct_delta, log.agi_pct_delta) == actual
    assert log.soul_delta == log.cultivation_delta == 0
    for delta in actual:
        if delta:
            assert f"{delta:+d}%" in log.result_text
    if not any(actual):
        assert "%" not in log.result_text
    if actual == (1, 1, 1):
        assert log.result_text.count("+1%") == 3
        assert all(f"+{delta}%" not in log.result_text for delta in rolled)


@pytest.mark.parametrize("elapsed_minutes", [10, 120])
def test_capped_trip_has_no_phantom_gain_or_empty_highlight(services, monkeypatch, elapsed_minutes) -> None:
    travel = TravelService(services.fate, random.Random(42))
    now = now_shanghai()
    character = Character(
        is_traveling=True,
        travel_started_at=now - timedelta(minutes=elapsed_minutes),
        travel_duration_minutes=120,
        travel_atk_pct=75,
        travel_def_pct=75,
        travel_agi_pct=50,
    )
    monkeypatch.setattr(travel, "_roll_event", lambda: _event(2, 3, 4))

    settlement = travel.stop_travel(character, now=now)

    assert settlement.success
    assert settlement.settled_events == elapsed_minutes // 10
    assert settlement.completed == (elapsed_minutes == 120)
    assert (settlement.total_atk_pct, settlement.total_def_pct, settlement.total_agi_pct) == (0, 0, 0)
    assert all("%" not in log.result_text for log in settlement.logs)
    assert "%" not in character.last_highlight_text
    assert "\uff0c\u3002" not in character.last_highlight_text
    assert not character.is_traveling
    assert _bonuses(character) == (75, 75, 50)


def test_trip_caps_each_event_and_allows_penalties_then_recovery(services, monkeypatch) -> None:
    travel = TravelService(services.fate, random.Random(42))
    now = now_shanghai()
    character = Character(
        is_traveling=True,
        travel_started_at=now - timedelta(minutes=40),
        travel_duration_minutes=120,
        travel_atk_pct=74,
        travel_def_pct=74,
        travel_agi_pct=49,
    )
    events = iter((_event(3, 3, 3), _event(3, 3, 3), _event(-2, -2, -2), _event(3, 3, 3)))
    monkeypatch.setattr(travel, "_roll_event", lambda: next(events))

    settlement = travel.stop_travel(character, now=now)

    assert settlement.success
    assert settlement.settled_events == 4
    assert settlement.settled_minutes == 40
    assert not settlement.completed
    assert _bonuses(character) == (75, 75, 50)
    assert (settlement.total_atk_pct, settlement.total_def_pct, settlement.total_agi_pct) == (1, 1, 1)
    assert [(log.atk_pct_delta, log.def_pct_delta, log.agi_pct_delta) for log in settlement.logs] == [
        (1, 1, 1),
        (0, 0, 0),
        (-2, -2, -2),
        (2, 2, 2),
    ]
    assert character.last_highlight_text.count("+1%") == 3


async def test_capped_rewards_keep_resources_and_honors_and_persist(session_factory, services, monkeypatch) -> None:
    travel = TravelService(services.fate, random.Random(42))
    event = _event(3, 3, 3)
    event.soul_min = event.soul_max = 5
    event.cultivation_pct_min = event.cultivation_pct_max = 5
    event.honor_tag = "test_honor"
    event.honor_duplicate_soul = 10
    monkeypatch.setattr(travel, "_roll_event", lambda: event)
    now = now_shanghai()
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 103, "Capped traveler")
        character = creation.character
        character.fate_key = "lingtaijingshou"
        character.travel_atk_pct = 74
        character.travel_def_pct = 75
        character.travel_agi_pct = 50
        expected_cultivation = 2 * max(1, int(services.character.get_stage(character).cultivation_max * 5 / 100))
        assert travel.start_travel(character, now=now - timedelta(minutes=20)).success

        settlement = travel.stop_travel(character, now=now)

        assert (settlement.total_atk_pct, settlement.total_def_pct, settlement.total_agi_pct) == (1, 0, 0)
        assert settlement.total_soul == 20
        assert settlement.total_cultivation == expected_cultivation
        assert settlement.gained_honor_tags == ("test_honor",)
        assert [log.honor_gained for log in settlement.logs] == [True, False]
        snapshot = services.character.build_snapshot(character)
        embed = build_travel_settlement_embed(snapshot, settlement)
        fields = "\n".join(field.value for field in embed.fields)
        assert "+1%" in fields
        assert "+3%" not in fields
        character_id = character.id
        await session.commit()

    async with session_factory() as session:
        character = await services.character.get_character_by_id(session, character_id)
        assert _bonuses(character) == (75, 75, 50)
        assert character.artifact.soul_shards == 20
        assert character.cultivation == expected_cultivation
        assert character.stored_honor_tags() == ("test_honor",)


@pytest.mark.parametrize(
    ("stored", "effective"),
    [
        ((175, 175, 150), (75, 75, 50)),
        ((75, 75, 50), (75, 75, 50)),
        ((175, -20, 150), (75, -20, 50)),
        ((-10, -20, -30), (-10, -20, -30)),
    ],
)
async def test_stats_panel_and_combat_use_caps_before_database_upgrade(
    session_factory, services, stored, effective
) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 101, "Legacy traveler")
        character = creation.character
        character.fate_key = "lingtaijingshou"
        character.artifact.atk_bonus = 100
        character.artifact.def_bonus = 200
        character.artifact.agi_bonus = 300
        character.travel_atk_pct, character.travel_def_pct, character.travel_agi_pct = stored
        stage = services.character.get_stage(character)
        expected_stats = (
            int((stage.base_atk + 100) * (1 + effective[0] / 100)),
            int((stage.base_def + 200) * (1 + effective[1] / 100)),
            int((stage.base_agi + 300) * (1 + effective[2] / 100)),
        )

        stats = services.character.calculate_total_stats(character)

        assert (stats.atk, stats.defense, stats.agility) == expected_stats
        assert _bonuses(character) == stored
        snapshot = services.character.build_snapshot(character)
        assert _bonuses(snapshot) == effective
        assert _bonuses(character) == effective
        assert (snapshot.total_atk, snapshot.total_def, snapshot.total_agi) == expected_stats
        assert snapshot.combat_power == stats.combat_power
        embed = build_travel_embed(snapshot)
        fields = "\n".join(field.value for field in embed.fields)
        for value in effective:
            assert f"{value:+d}%" in fields

        character.travel_atk_pct, character.travel_def_pct, character.travel_agi_pct = stored
        fighter = services.character.build_combatant(character)
        assert (fighter.atk, fighter.defense, fighter.agility) == expected_stats
        assert fighter.max_hp == expected_stats[1] * 10
        assert services.character.refresh_combat_power(character) == stats.combat_power


@pytest.mark.parametrize("lookup", ["discord_id", "id", "rank", "list"])
async def test_character_lookup_persists_normalization(session_factory, services, lookup) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 102, "Legacy traveler")
        character = creation.character
        character.travel_atk_pct = 175
        character.travel_def_pct = -20
        character.travel_agi_pct = 150
        character_id = character.id
        rank = character.current_ladder_rank
        await session.commit()

    async with session_factory() as session:
        if lookup == "discord_id":
            character = await services.character.get_character_by_discord_id(session, 102)
        elif lookup == "id":
            character = await services.character.get_character_by_id(session, character_id)
        elif lookup == "rank":
            character = await services.character.get_character_by_rank(session, rank)
        else:
            [character] = await services.character.list_characters(session)
        assert _bonuses(character) == (75, -20, 50)
        await session.commit()

    async with session_factory() as session:
        character = await session.get(Character, character_id)
        assert _bonuses(character) == (75, -20, 50)


async def test_startup_truncates_persisted_excess_and_is_idempotent(tmp_path, services) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'travel_caps.sqlite3').as_posix()}"
    engine, session_factory = create_engine_and_session_factory(database_url)
    initial = [(175, 175, 150), (74, 74, 49), (75, 75, 50), (-10, -20, -30), (175, -20, 150)]
    expected = [(75, 75, 50), (74, 74, 49), (75, 75, 50), (-10, -20, -30), (75, -20, 50)]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for index, bonuses in enumerate(initial):
                creation = await services.character.get_or_create_character(session, 200 + index, f"Traveler {index}")
                character = creation.character
                character.travel_atk_pct, character.travel_def_pct, character.travel_agi_pct = bonuses
                character.is_npc = index == 0
            await session.commit()
    finally:
        await engine.dispose()

    engine, _ = create_engine_and_session_factory(database_url)
    try:
        await init_models(engine)
    finally:
        await engine.dispose()

    engine, session_factory = create_engine_and_session_factory(database_url)
    try:
        for _ in range(2):
            async with session_factory() as session:
                characters = (await session.scalars(select(Character).order_by(Character.id))).all()
                assert [_bonuses(character) for character in characters] == expected
            await ensure_schema_compatibility(engine)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("missing_columns", "expected"),
    [
        (("travel_atk_pct", "travel_def_pct", "travel_agi_pct"), (0, 0, 0)),
        (("travel_agi_pct",), (75, 75, 0)),
    ],
)
async def test_startup_adds_missing_travel_columns_before_truncation(
    tmp_path, services, missing_columns, expected
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'legacy_travel.sqlite3').as_posix()}"
    engine, session_factory = create_engine_and_session_factory(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            creation = await services.character.get_or_create_character(session, 300, "Legacy traveler")
            character = creation.character
            character.travel_atk_pct = 175
            character.travel_def_pct = 175
            character.travel_agi_pct = 150
            character.cultivation = 12
            character_id = character.id
            await session.commit()
        async with engine.begin() as connection:
            for column in missing_columns:
                await connection.execute(text(f"ALTER TABLE characters DROP COLUMN {column}"))

        await init_models(engine)
        await ensure_schema_compatibility(engine)

        async with engine.connect() as connection:
            columns = (await connection.execute(text("PRAGMA table_info(characters)"))).mappings().all()
            for name in missing_columns:
                column = next(column for column in columns if column["name"] == name)
                assert column["notnull"] == 1
                assert column["dflt_value"] == "0"
        async with session_factory() as session:
            character = await session.get(Character, character_id)
            assert _bonuses(character) == expected
            assert character.cultivation == 12
    finally:
        await engine.dispose()
