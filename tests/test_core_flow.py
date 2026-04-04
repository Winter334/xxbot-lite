from __future__ import annotations

from datetime import timedelta

import pytest

from bot.utils.time_utils import now_shanghai


@pytest.mark.asyncio
async def test_character_creation_assigns_fate_artifact_and_rank(session_factory, services) -> None:
    async with session_factory() as session:
        result = await services.character.get_or_create_character(session, 1001, "青崖")
        await session.commit()
        character = result.character
        assert result.created is True
        assert character.current_ladder_rank == 1
        assert character.artifact is not None
        assert character.fate_key
        assert character.combat_power > 0


@pytest.mark.asyncio
async def test_idle_settlement_caps_at_stage_max_and_recovers_qi(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1002, "寒松")
        character = creation.character
        now = now_shanghai()
        character.current_qi = 1
        character.last_qi_recovered_at = now - timedelta(hours=2)
        character.last_idle_at = now - timedelta(hours=25)
        settlement = services.idle.settle(character, now=now)
        await session.commit()

        assert settlement.recovered_qi == 5
        assert character.current_qi == character.qi_max
        assert character.cultivation == services.character.get_stage(character).cultivation_max


@pytest.mark.asyncio
async def test_tower_and_breakthrough_progress(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1003, "白槐")
        character = creation.character
        tower_result = services.tower.run_tower(character)
        assert tower_result.success is True
        assert character.current_qi == 5
        assert character.highest_floor >= 1

        stage = services.character.get_stage(character)
        character.cultivation = stage.cultivation_max
        character.highest_floor = 25
        breakthrough = services.breakthrough.attempt_breakthrough(character)
        await session.commit()

        assert breakthrough.success is True
        assert character.stage_index == 2


@pytest.mark.asyncio
async def test_ladder_challenge_swaps_rank_on_victory(session_factory, services) -> None:
    async with session_factory() as session:
        defender = (await services.character.get_or_create_character(session, 2001, "守擂者")).character
        challenger = (await services.character.get_or_create_character(session, 2002, "夺位者")).character
        challenger.realm_key = "zhuji"
        challenger.realm_index = 2
        challenger.stage_key = "early"
        challenger.stage_index = 1
        services.character.refresh_combat_power(challenger)

        result = await services.ladder.challenge(session, challenger, 1)
        await session.commit()

        assert result.success is True
        assert result.battle is not None
        assert result.battle.challenger_won is True
        assert challenger.current_ladder_rank == 1
        assert defender.current_ladder_rank == 2
