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
        assert settlement.gained_soul >= 0
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


@pytest.mark.asyncio
async def test_combat_fate_uses_split_bonus_per_stat(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 3001, "玄陵")
        character = creation.character

        character.fate_key = "longhubingqu"  # 稀有双属性，总档位 6%，应分摊为每项 3%
        stats = services.character.calculate_total_stats(character)
        stage = services.character.get_stage(character)

        assert stats.atk == int(stage.base_atk * 1.03)
        assert stats.defense == int(stage.base_def * 1.03)
        assert stats.agility == stage.base_agi


@pytest.mark.asyncio
async def test_fate_effect_summary_matches_split_bonus_display(session_factory, services) -> None:
    fate = services.fate.get_fate("hunyuanwugou")
    assert fate.effect_summary() == "杀伐 + 护体 + 身法 +4%"


@pytest.mark.asyncio
async def test_fortune_fate_summary_uses_bonus_drop_wording(session_factory, services) -> None:
    fate = services.fate.get_fate("ziweichuizhao")
    assert fate.effect_summary() == "首通额外掉落率 +12%"
