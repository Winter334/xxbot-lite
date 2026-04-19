from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.data.realms import get_stage
from bot.data.spirits import SpiritPowerEntry
from bot.models.world_resource_site import WorldResourceSite
from bot.services.travel_service import TravelService
from bot.ui.panel import build_ladder_round_embed, build_panel_embed, build_retreat_embed
from bot.utils.time_utils import now_shanghai


class TravelRoller:
    def __init__(self, random_values, event_ids, int_values) -> None:
        self._random_values = iter(random_values)
        self._event_ids = iter(event_ids)
        self._int_values = iter(int_values)

    def random(self) -> float:
        return next(self._random_values)

    def choices(self, population, weights, k=1):
        event_id = next(self._event_ids)
        for event in population:
            if event.event_id == event_id:
                return [event]
        raise AssertionError(f"event {event_id} not in current pool")

    def randint(self, start: int, end: int) -> int:
        value = next(self._int_values)
        assert start <= value <= end
        return value


class CombatRoller:
    def __init__(self, random_values) -> None:
        self._random_values = iter(random_values)

    def random(self) -> float:
        return next(self._random_values)


def _set_stage(character, realm_key: str, stage_key: str) -> None:
    stage = get_stage(realm_key, stage_key)
    character.realm_key = stage.realm_key
    character.realm_index = stage.realm_index
    character.stage_key = stage.stage_key
    character.stage_index = stage.stage_index


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
        assert character.faction == "neutral"
        assert 1 <= character.luck <= 99


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
        character.fate_key = "jinshicangfeng"
        now = now_shanghai()
        services.character.start_retreat(character)
        character.last_idle_at = now - timedelta(minutes=60)
        settlement = services.idle.settle_retreat(character, now=now)
        await session.commit()

        assert settlement.gained_cultivation == 30
        assert settlement.gained_luck == 0


@pytest.mark.asyncio
async def test_righteous_retreat_gains_luck_linearly(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1007, "清衡")
        character = creation.character
        success, _ = services.faction.join_faction(character, "righteous")
        assert success is True
        now = now_shanghai()
        services.character.start_retreat(character)
        character.last_idle_at = now - timedelta(minutes=90)
        settlement = services.idle.settle_retreat(character, now=now)
        await session.commit()

        assert settlement.gained_luck == 45
        assert character.luck >= 45


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
async def test_soul_retreat_grants_30_soul_per_hour_and_a_bit_of_cultivation(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1018, "熔星")
        character = creation.character
        now = now_shanghai()
        start = services.character.start_retreat(character, mode="soul")
        character.last_idle_at = now - timedelta(hours=1)
        settlement = services.idle.settle_retreat(character, now=now)
        stop = services.character.stop_retreat(character, settlement)
        await session.commit()

        assert start.success is True
        assert settlement.retreat_mode == "soul"
        assert settlement.gained_soul == 30
        assert settlement.gained_cultivation > 0
        assert settlement.gained_cultivation < 30
        assert "炼魂" in stop.message
        assert character.retreat_mode == "cultivation"


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
        character.fate_key = "lingtaijingshou"
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
async def test_travel_stat_events_use_low_probability_gate(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1015, "行客")
        character = creation.character

        non_stat_service = TravelService(services.fate, rng=TravelRoller([0.99], ["soul_1"], [2]))
        non_stat_log = non_stat_service._resolve_event(character)
        assert non_stat_log.atk_pct_delta == 0
        assert non_stat_log.def_pct_delta == 0
        assert non_stat_log.agi_pct_delta == 0

        stat_service = TravelService(services.fate, rng=TravelRoller([0.0], ["atk_up"], [2]))
        stat_log = stat_service._resolve_event(character)
        await session.commit()

        assert stat_log.atk_pct_delta == 2
        assert character.travel_atk_pct == 2


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
        assert "☯ 阵营信息" in field_names
        travel_field = next(field for field in embed.fields if field.name == "🧭 游历遗痕")
        assert "+3%" in travel_field.value
        assert "-1%" in travel_field.value


@pytest.mark.asyncio
async def test_panel_embed_shows_soul_retreat_state(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1019, "照炉")
        character = creation.character
        character.is_retreating = True
        character.retreat_mode = "soul"
        snapshot = services.character.build_snapshot(character, idle_minutes=60)
        embed = build_panel_embed(snapshot)
        retreat_embed = build_retreat_embed(snapshot)
        await session.commit()

        artifact_field = next(field for field in embed.fields if field.name == "🗿 本命")
        assert "炼魂中" in artifact_field.value
        assert retreat_embed.title.endswith("洞府")
        assert retreat_embed.fields[1].name == "洞府气象"


@pytest.mark.asyncio
async def test_spirit_nurture_collects_after_unlock_and_boosts_artifact_power(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1016, "灵工")
        character = creation.character
        character.artifact.reinforce_level = 30
        character.artifact.atk_bonus = 12000
        character.artifact.def_bonus = 9000
        character.artifact.agi_bonus = 6000
        character.artifact.soul_shards = 200
        now = now_shanghai()

        start = services.spirit.start_nurture(character.artifact, now=now)
        early_collect = services.spirit.collect_result(character.artifact, now=now + timedelta(minutes=30))
        final_collect = services.spirit.collect_result(character.artifact, now=now + timedelta(minutes=60))
        await session.commit()

        assert start.success is True
        assert early_collect.success is False
        assert final_collect.success is True
        assert services.spirit.get_current_spirit(character.artifact) is not None
        assert character.artifact.spirit_name
        assert services.spirit.artifact_power(character.artifact) > (12000 + 9000 + 6000)


@pytest.mark.asyncio
async def test_panel_embed_shows_spirit_summary(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 1017, "灵照")
        character = creation.character
        character.artifact.reinforce_level = 30
        character.artifact.atk_bonus = 12000
        character.artifact.def_bonus = 9000
        character.artifact.agi_bonus = 6000
        character.artifact.soul_shards = 200
        now = now_shanghai()
        services.spirit.start_nurture(character.artifact, now=now)
        services.spirit.collect_result(character.artifact, now=now + timedelta(minutes=60))
        snapshot = services.character.build_snapshot(character)
        embed = build_panel_embed(snapshot)
        await session.commit()

        artifact_field = next(field for field in embed.fields if field.name == "🗿 本命")
        assert "器灵：" in artifact_field.value
        assert "神通：" in artifact_field.value


@pytest.mark.asyncio
async def test_niepan_spirit_power_revives_once_in_combat(session_factory, services) -> None:
    roller = CombatRoller([0.99] * 40)
    attacker = services.combat.create_combatant(name="斩者", atk=500, defense=30, agility=50)
    defender = services.combat.create_combatant(
        name="守者",
        atk=20,
        defense=20,
        agility=10,
        spirit_power=SpiritPowerEntry("niepan", {"heal_pct": 50, "reduce_pct": 80}),
    )

    result = services.combat.run_battle(attacker, defender, rng=roller)

    assert any(log.text and "涅槃再起" in log.text for log in result.logs)


@pytest.mark.asyncio
async def test_create_and_join_sect_tracks_identity(session_factory, services) -> None:
    async with session_factory() as session:
        creator = (await services.character.get_or_create_character(session, 8001, "宗主")).character
        _set_stage(creator, "zhuji", "early")
        success, _ = await services.sect.create_sect(session, creator, "青岚宗")
        member = (await services.character.get_or_create_character(session, 8002, "门徒")).character
        join_success, _ = await services.sect.join_sect(session, member, creator.sect_id)
        sect_name, sect_role = await services.sect.get_member_identity(session, creator)
        await session.commit()

        assert success is True
        assert join_success is True
        assert sect_name == "青岚宗"
        assert sect_role == "宗主"
        assert member.sect_id == creator.sect_id


@pytest.mark.asyncio
async def test_sect_overview_contains_top_25_members(session_factory, services) -> None:
    async with session_factory() as session:
        leader = (await services.character.get_or_create_character(session, 8100, "宗主甲")).character
        _set_stage(leader, "zhuji", "early")
        success, _ = await services.sect.create_sect(session, leader, "太玄门")
        assert success is True
        for index in range(26):
            member = (await services.character.get_or_create_character(session, 8101 + index, f"门徒{index:02d}")).character
            await services.sect.join_sect(session, member, leader.sect_id)
            member.sect_contribution_weekly = 100 - index
            member.sect_contribution_total = 1000 - index
        overview = await services.sect.get_sect_overview(session, leader)
        await session.commit()

        assert overview is not None
        assert len(overview.members) == 25
        assert overview.members[0].display_name == "宗主甲"


@pytest.mark.asyncio
async def test_sect_site_action_consumes_qi_and_claims_unowned_site(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 8003, "争地")).character
        _set_stage(character, "zhuji", "early")
        await services.sect.create_sect(session, character, "断岳门")
        await services.sect.ensure_sites(session)
        site = (await session.scalars(select(WorldResourceSite).limit(1))).one()

        first = await services.sect.perform_site_action(session, character, site.id, "contest")
        second = await services.sect.perform_site_action(session, character, site.id, "contest")
        await session.commit()

        assert first.success is True
        assert second.success is True
        assert first.qi_before == 6
        assert first.qi_after == 5
        assert second.qi_before == 5
        assert second.qi_after == 4
        assert site.owner_sect_id == character.sect_id
        assert character.sect_bound_site_id == site.id
        assert character.sect_bound_site_role == "guard"
        assert character.sect_contribution_daily > 0


@pytest.mark.asyncio
async def test_sect_switching_sites_moves_bound_role(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 8004, "换位")).character
        _set_stage(character, "zhuji", "early")
        await services.sect.create_sect(session, character, "玄石门")
        await services.sect.ensure_sites(session)
        sites = list((await session.scalars(select(WorldResourceSite).order_by(WorldResourceSite.id.asc()))).all())
        assert len(sites) >= 2
        first_site, second_site = sites[:2]
        first_site.owner_sect_id = character.sect_id
        second_site.owner_sect_id = character.sect_id

        first_result = await services.sect.perform_site_action(session, character, first_site.id, "transport")
        second_result = await services.sect.perform_site_action(session, character, second_site.id, "guard")
        await session.commit()

        first_state = services.sect._load_state(first_site.state_json)
        second_state = services.sect._load_state(second_site.state_json)

        assert first_result.success is True
        assert second_result.success is True
        assert character.sect_bound_site_id == second_site.id
        assert character.sect_bound_site_role == "guard"
        assert character.id not in first_state["transport_member_ids"]
        assert character.id in second_state["guard_member_ids"]


@pytest.mark.asyncio
async def test_enemy_site_progress_is_tracked_per_sect(session_factory, services) -> None:
    async with session_factory() as session:
        defender = (await services.character.get_or_create_character(session, 8007, "守脉者")).character
        attacker_a = (await services.character.get_or_create_character(session, 8008, "夺旗甲")).character
        attacker_b = (await services.character.get_or_create_character(session, 8009, "夺旗乙")).character
        _set_stage(defender, "zhuji", "early")
        _set_stage(attacker_a, "dujie", "perfect")
        _set_stage(attacker_b, "dujie", "perfect")
        await services.sect.create_sect(session, defender, "守山门")
        await services.sect.create_sect(session, attacker_a, "裂空门")
        await services.sect.create_sect(session, attacker_b, "照夜门")
        await services.sect.ensure_sites(session)
        site = (await session.scalars(select(WorldResourceSite).limit(1))).one()
        site.owner_sect_id = defender.sect_id

        guard_result = await services.sect.perform_site_action(session, defender, site.id, "guard")
        attack_a_result = await services.sect.perform_site_action(session, attacker_a, site.id, "contest")
        attack_b_result = await services.sect.perform_site_action(session, attacker_b, site.id, "contest")
        await session.commit()

        state = services.sect._load_state(site.state_json)

        assert guard_result.success is True
        assert attack_a_result.success is True
        assert attack_b_result.success is True
        assert state["attack_progress"][attacker_a.sect_id] == 1
        assert state["attack_progress"][attacker_b.sect_id] == 1


@pytest.mark.asyncio
async def test_sect_settlement_rewards_use_transport_bonus(session_factory, services) -> None:
    async with session_factory() as session:
        guarder = (await services.character.get_or_create_character(session, 8010, "矿守")).character
        transporter = (await services.character.get_or_create_character(session, 8011, "运石")).character
        _set_stage(guarder, "zhuji", "early")
        _set_stage(transporter, "zhuji", "early")
        await services.sect.create_sect(session, guarder, "玄石门")
        await services.sect.join_sect(session, transporter, guarder.sect_id)
        await services.sect.ensure_sites(session)
        site = (await session.scalars(select(WorldResourceSite).order_by(WorldResourceSite.id.asc()))).first()
        assert site is not None
        site.site_type = "lingshi"
        site.owner_sect_id = guarder.sect_id
        site.settlement_day = now_shanghai().date() - timedelta(days=1)
        guarder.sect_joined_at = now_shanghai() - timedelta(days=2)
        transporter.sect_joined_at = now_shanghai() - timedelta(days=2)
        guarder.sect_bound_site_id = site.id
        guarder.sect_bound_site_role = "guard"
        transporter.sect_bound_site_id = site.id
        transporter.sect_bound_site_role = "transport"
        site.state_json = services.sect._dump_state(
            {
                "attack_progress": {},
                "attack_members": {},
                "guard_member_ids": [guarder.id],
                "transport_member_ids": [transporter.id],
            }
        )

        notices = await services.sect.settle_sites_if_needed(session)
        await session.commit()

        assert notices.get(guarder.id)
        assert guarder.lingshi == 220
        assert guarder.artifact.soul_shards == 11
        assert transporter.lingshi == 220
        assert transporter.artifact.soul_shards == 11


@pytest.mark.asyncio
async def test_bounty_hunt_grants_lingshi(session_factory, services) -> None:
    async with session_factory() as session:
        hunter = (await services.character.get_or_create_character(session, 8005, "悬赏客")).character
        target = (await services.character.get_or_create_character(session, 8006, "悬首")).character
        services.faction.join_faction(hunter, "righteous")
        services.faction.join_faction(target, "demonic")
        hunter.realm_key = "zhuji"
        hunter.realm_index = 2
        target.bounty_soul = 12

        result = services.faction.challenge_bounty(hunter, target)
        await session.commit()

        assert result.success is True
        assert result.lingshi_delta == 120
        assert hunter.lingshi >= 120


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
        character.fate_key = "lingtaijingshou"
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

        character.fate_key = "fengleibingzuo"
        stats = services.character.calculate_total_stats(character)
        stage = services.character.get_stage(character)

        assert stats.atk == int(stage.base_atk * 1.15)
        assert stats.defense == stage.base_def
        assert stats.agility == int(stage.base_agi * 1.15)


@pytest.mark.asyncio
async def test_fate_effect_summary_matches_split_bonus_display(session_factory, services) -> None:
    fate = services.fate.get_fate("hunyuanbaopu")
    assert fate.effect_summary() == "杀伐 +18%，护体 +18%，身法 +18%"


@pytest.mark.asyncio
async def test_soul_fate_summary_uses_general_gain_wording(session_factory, services) -> None:
    fate = services.fate.get_fate("ziyuanyingming")
    assert fate.effect_summary() == "器魂获取 +40%"


@pytest.mark.asyncio
async def test_rewrite_fate_costs_luck_and_changes_fate(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 5001, "命客")
        character = creation.character
        character.luck = 200
        old_fate = character.fate_key

        result = await services.character.rewrite_fate(session, character)
        await session.commit()

        assert result.success is True
        assert character.luck == 100
        assert character.fate_key != old_fate


@pytest.mark.asyncio
async def test_bounty_hunt_clears_bounty_and_grants_virtue(session_factory, services) -> None:
    async with session_factory() as session:
        hunter = (await services.character.get_or_create_character(session, 6001, "正修")).character
        target = (await services.character.get_or_create_character(session, 6002, "魔修")).character
        services.faction.join_faction(hunter, "righteous")
        services.faction.join_faction(target, "demonic")
        hunter.realm_key = "zhuji"
        hunter.realm_index = 2
        target.bounty_soul = 12

        result = services.faction.challenge_bounty(hunter, target)
        await session.commit()

        assert result.success is True
        assert hunter.virtue == 12
        assert hunter.luck >= 10
        assert target.bounty_soul == 0


@pytest.mark.asyncio
async def test_robbery_on_same_demonic_target_halves_rewards(session_factory, services) -> None:
    async with session_factory() as session:
        robber = (await services.character.get_or_create_character(session, 7001, "魔甲")).character
        target = (await services.character.get_or_create_character(session, 7002, "魔乙")).character
        services.faction.join_faction(robber, "demonic")
        services.faction.join_faction(target, "demonic")
        robber.realm_key = "zhuji"
        robber.realm_index = 2
        target.artifact.soul_shards = 20
        target.luck = 120

        result = services.faction.rob(robber, target)
        await session.commit()

        assert result.success is True
        assert result.same_faction_halved is True
        assert result.soul_delta == 1
        assert result.luck_delta == 9
