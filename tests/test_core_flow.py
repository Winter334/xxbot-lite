from __future__ import annotations

from datetime import timedelta

import pytest

from bot.ui.panel import build_panel_embed
from bot.utils.time_utils import now_shanghai


@pytest.mark.asyncio
async def test_character_creation_broadcasts_fate_and_artifact(session_factory, services) -> None:
    async with session_factory() as session:
        result = await services.character.get_or_create_character(session, 1001, "青崖")
        await session.commit()
        assert result.created is True
        assert result.broadcast_needed is True
        assert result.broadcast_text is not None
        assert "命格" in result.broadcast_text
        assert "本命法宝" in result.broadcast_text


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
        services.character.start_retreat(character)
        character.last_idle_at = now - timedelta(hours=25)
        settlement = services.idle.settle_retreat(character, now=now)
        await session.commit()

        assert settlement.recovered_qi == 5
        assert settlement.gained_soul >= 0
        assert character.current_qi == character.qi_max
        assert character.cultivation == services.character.get_stage(character).cultivation_max


@pytest.mark.asyncio
async def test_idle_early_realm_has_accelerated_progress(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1004, "松岚")
        character = creation.character
        now = now_shanghai()
        services.character.start_retreat(character)
        character.last_idle_at = now - timedelta(minutes=60)
        settlement = services.idle.settle_retreat(character, now=now)
        await session.commit()

        assert settlement.gained_cultivation == 30


@pytest.mark.asyncio
async def test_manual_retreat_blocks_tower_until_exit(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1005, "流云")
        character = creation.character
        start = services.character.start_retreat(character)
        tower_result = services.tower.run_tower(character)
        settlement = services.idle.settle_retreat(character, now=now_shanghai() + timedelta(minutes=30))
        stop = services.character.stop_retreat(character, settlement)
        await session.commit()

        assert start.success is True
        assert tower_result.success is False
        assert "出关" in tower_result.message
        assert stop.success is True
        assert character.is_retreating is False
        assert settlement.gained_cultivation > 0


@pytest.mark.asyncio
async def test_travel_and_retreat_are_mutually_exclusive(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1010, "游客")
        character = creation.character
        travel_start = services.travel.start_travel(character, 60)
        retreat_start = services.character.start_retreat(character)
        tower_result = services.tower.run_tower(character)
        await session.commit()

        assert travel_start.success is True
        assert retreat_start.success is False
        assert character.is_traveling is True
        assert tower_result.success is False


@pytest.mark.asyncio
async def test_travel_settlement_uses_30_minute_cycles_and_caps_at_10(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1011, "远行")
        character = creation.character
        now = now_shanghai()
        services.travel.start_travel(character, 300, now=now)
        character.travel_started_at = now - timedelta(hours=8)
        settlement = services.travel.stop_travel(character, now=now)
        await session.commit()

        assert settlement.success is True
        assert settlement.settled_events == 10
        assert settlement.settled_minutes == 300
        assert character.is_traveling is False


@pytest.mark.asyncio
async def test_travel_permanent_stat_bonus_affects_total_stats(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1012, "留痕")
        character = creation.character
        stage = services.character.get_stage(character)
        character.travel_atk_pct = 10
        character.travel_def_pct = -10
        character.travel_agi_pct = 20
        stats = services.character.calculate_total_stats(character)
        await session.commit()

        assert stats.atk == int(stage.base_atk * 1.10)
        assert stats.defense == int(stage.base_def * 0.90)
        assert stats.agility == int(stage.base_agi * 1.20)


@pytest.mark.asyncio
async def test_travel_negative_event_can_apply(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1013, "逢厄")
        character = creation.character
        services.travel.start_travel(character, 30)
        character.travel_started_at = now_shanghai() - timedelta(minutes=30)
        found_negative = False
        for _ in range(20):
            character.is_traveling = True
            character.travel_duration_minutes = 30
            character.travel_started_at = now_shanghai() - timedelta(minutes=30)
            settlement = services.travel.stop_travel(character)
            if any(
                log.soul_delta < 0
                or log.cultivation_delta < 0
                or log.atk_pct_delta < 0
                or log.def_pct_delta < 0
                or log.agi_pct_delta < 0
                for log in settlement.logs
            ):
                found_negative = True
                break
        await session.commit()

        assert found_negative is True


@pytest.mark.asyncio
async def test_panel_embed_shows_travel_marks(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1014, "留名")
        character = creation.character
        character.travel_atk_pct = 3
        character.travel_def_pct = -1
        character.travel_agi_pct = 2
        snapshot = services.character.build_snapshot(character)
        embed = build_panel_embed(snapshot)
        await session.commit()

        field_names = [field.name for field in embed.fields]
        assert "🧭 游历遗痕" in field_names
        travel_field = next(field for field in embed.fields if field.name == "🧭 游历遗痕")
        assert "+3%" in travel_field.value
        assert "-1%" in travel_field.value


@pytest.mark.asyncio
async def test_not_retreating_only_recovers_qi_without_cultivation(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1006, "星阙")
        character = creation.character
        now = now_shanghai()
        character.current_qi = 2
        character.last_qi_recovered_at = now - timedelta(minutes=60)
        character.last_idle_at = now - timedelta(hours=8)
        settlement = services.idle.settle_retreat(character, now=now)
        await session.commit()

        assert settlement.recovered_qi > 0
        assert settlement.gained_cultivation == 0
        assert settlement.gained_soul == 0
        assert character.is_retreating is False


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
async def test_title_bonus_applies_small_global_bonus(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 4001, "玄霄")
        character = creation.character
        stage = services.character.get_stage(character)
        base_atk, base_def, base_agi = stage.base_atk, stage.base_def, stage.base_agi
        character.title = "独断万古"
        stats = services.character.calculate_total_stats(character)
        assert stats.atk == int(base_atk * 1.025)
        assert stats.defense == int(base_def * 1.025)
        assert stats.agility == int(base_agi * 1.025)


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
