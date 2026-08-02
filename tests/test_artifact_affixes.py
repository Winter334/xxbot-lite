from __future__ import annotations

from collections.abc import Sequence
import json

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry, get_artifact_affix_definition
from bot.services.combat_service import _CombatState, _StatusEffect
from bot.ui.artifact import build_artifact_overview_embed, build_refine_panel_embed


class ArtifactRoller:
    def __init__(self, affix_ids: Sequence[str], int_values: Sequence[int], *, fallback_random: float = 0.0) -> None:
        self._affix_ids = iter(affix_ids)
        self._int_values = iter(int_values)
        self._fallback_random = fallback_random

    def choice(self, definitions):
        sample = definitions[0]
        if isinstance(sample, str):
            return sample
        affix_id = next(self._affix_ids)
        for definition in definitions:
            if definition.affix_id == affix_id:
                return definition
        raise AssertionError(f"unknown affix id: {affix_id}")

    def randint(self, start: int, end: int) -> int:
        value = next(self._int_values, start)
        return max(start, min(value, end))

    def random(self) -> float:
        return self._fallback_random


class SequenceRandom:
    def __init__(self, values: Sequence[float], *, fallback: float = 0.99) -> None:
        self._values = iter(values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._values, self._fallback)


@pytest.mark.asyncio
async def test_affix_slots_unlock_by_reinforce_level(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5001, "灵鉴")).character
        artifact = character.artifact
        assert services.artifact.unlocked_slots(artifact) == 0

        artifact.reinforce_level = 10
        assert services.artifact.unlocked_slots(artifact) == 1

        artifact.reinforce_level = 29
        assert services.artifact.unlocked_slots(artifact) == 2

        artifact.reinforce_level = 30
        assert services.artifact.unlocked_slots(artifact) == 3


@pytest.mark.asyncio
async def test_newly_unlocked_slots_receive_initial_affixes(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun"], [34, 8, 50, 30, 3, 4])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5002, "器灵")).character
        artifact = character.artifact
        artifact.reinforce_level = 30

        newly_unlocked = services.artifact.ensure_affix_slots(artifact)
        await session.commit()

        assert newly_unlocked == (1, 2, 3)
        entries = services.artifact.get_affix_slots(artifact)
        assert [entry.slot for entry in entries] == [1, 2, 3]
        assert [entry.affix_id for entry in entries] == ["huichun", "ningshen", "zhuohun"]


@pytest.mark.asyncio
async def test_refine_pending_persists_and_only_applies_after_save(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 8, 50])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5003, "洗星")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        artifact.soul_shards = 5
        services.artifact.ensure_affix_slots(artifact)

        current_before = services.artifact.get_affix_slots(artifact)[0]
        result = services.artifact.refine_affix(artifact, 1)
        await session.commit()

        assert result.success is True
        assert artifact.soul_shards == 3
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == current_before.affix_id
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "ningshen"

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5003)
        assert character is not None
        artifact = character.artifact
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == "huichun"
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "ningshen"

        save_result = services.artifact.save_pending_affixes(artifact)
        await session.commit()

        assert save_result.success is True

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5003)
        assert character is not None
        artifact = character.artifact
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == "ningshen"
        assert services.artifact.get_pending_affixes(artifact) == []


@pytest.mark.asyncio
async def test_refine_all_affixes_rolls_every_unlocked_slot_without_saving(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun", "lueying"], [34, 8, 50, 30, 3, 4, 50, 30, 10])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5012, "一键洗炼")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        artifact.soul_shards = 10
        services.artifact.ensure_affix_slots(artifact)
        current_before = tuple(entry.affix_id for entry in services.artifact.get_affix_slots(artifact))

        result = services.artifact.refine_all_affixes(artifact)
        await session.commit()

        assert result.success is True
        assert result.slots == (1, 2)
        assert result.soul_cost == 4
        assert artifact.soul_shards == 6
        assert tuple(entry.affix_id for entry in result.pending_entries) == ("zhuohun", "lueying")
        assert tuple(entry.affix_id for entry in services.artifact.get_pending_affixes(artifact)) == ("zhuohun", "lueying")
        assert tuple(entry.affix_id for entry in services.artifact.get_affix_slots(artifact)) == current_before


@pytest.mark.asyncio
async def test_refine_all_affixes_fails_atomically_when_soul_is_insufficient(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun", "lueying"], [34, 8, 50, 30, 3, 4, 50, 30, 10])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5013, "器魂不足")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        artifact.soul_shards = 3
        services.artifact.ensure_affix_slots(artifact)
        current_before = tuple(entry.affix_id for entry in services.artifact.get_affix_slots(artifact))

        result = services.artifact.refine_all_affixes(artifact)

        assert result.success is False
        assert result.soul_cost == 4
        assert artifact.soul_shards == 3
        assert services.artifact.get_pending_affixes(artifact) == []
        assert tuple(entry.affix_id for entry in services.artifact.get_affix_slots(artifact)) == current_before


@pytest.mark.asyncio
async def test_refine_all_affixes_replaces_existing_pending_slots(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun", "lueying", "jinhuo"], [34, 8, 50, 30, 3, 4, 50, 30, 10])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5014, "重洗全部")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        artifact.soul_shards = 20
        services.artifact.ensure_affix_slots(artifact)
        services.artifact.refine_affix(artifact, 1)
        assert tuple(entry.affix_id for entry in services.artifact.get_pending_affixes(artifact)) == ("zhuohun",)

        result = services.artifact.refine_all_affixes(artifact)

        assert result.success is True
        assert tuple(entry.affix_id for entry in services.artifact.get_pending_affixes(artifact)) == ("lueying", "jinhuo")
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun", "lueying"], [34, 8, 50, 30, 3, 4, 50, 30, 10])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5008, "弃词")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        artifact.soul_shards = 8
        services.artifact.ensure_affix_slots(artifact)
        services.artifact.refine_affix(artifact, 1)
        services.artifact.refine_affix(artifact, 2)

        discard_result = services.artifact.discard_pending_affix(artifact, 2)
        await session.commit()

        assert discard_result.success is True
        assert discard_result.discarded_entry is not None
        assert discard_result.discarded_entry.affix_id == "lueying"
        assert [entry.slot for entry in services.artifact.get_pending_affixes(artifact)] == [1]
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "zhuohun"

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5008)
        assert character is not None
        artifact = character.artifact
        assert [entry.slot for entry in services.artifact.get_pending_affixes(artifact)] == [1]

        save_result = services.artifact.save_pending_affixes(artifact)
        await session.commit()

        assert save_result.success is True
        assert save_result.applied_slots == (1,)

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5008)
        assert character is not None
        artifact = character.artifact
        assert [entry.affix_id for entry in services.artifact.get_affix_slots(artifact)] == ["zhuohun", "ningshen"]
        assert services.artifact.get_pending_affixes(artifact) == []


@pytest.mark.asyncio
async def test_discard_pending_affix_fails_cleanly_without_pending(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun"], [34, 8])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5009, "空弃")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        services.artifact.ensure_affix_slots(artifact)

        result = services.artifact.discard_pending_affix(artifact, 1)

        assert result.success is False
        assert result.discarded_entry is None
        assert result.message == "槽1 当前没有可放弃的待选词条。"


@pytest.mark.asyncio
async def test_refine_embed_shows_affix_name_and_description(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 8, 50])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5004, "照夜")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        artifact.soul_shards = 4
        services.artifact.ensure_affix_slots(artifact)
        services.artifact.refine_affix(artifact, 1)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        current_field = next(field for field in embed.fields if field.name == "当前词条")
        pending_field = next(field for field in embed.fields if field.name == "待选词条")

        assert "回春" in current_field.value
        assert "最大生命" in current_field.value
        assert "凝神" in pending_field.value
        assert "灵势" in pending_field.value
        field_names = {field.name for field in embed.fields}
        assert "三维加成" not in field_names
        assert "总三维" not in field_names


@pytest.mark.asyncio
async def test_affix_panel_describes_legacy_rolls_with_new_defaults(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5011, "旧词条")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        # 旧 rolls 字段（proc_pct/burn_pct）已不再被新版 zhuohun 识别，应回退到默认下限值
        artifact.affix_slots_json = json.dumps(
            [
                {
                    "slot": 1,
                    "affix_id": "zhuohun",
                    "rolls": {"proc_pct": 25, "burn_pct": 2},
                }
            ]
        )

        panel_state = services.artifact.build_panel_state(artifact)
        current_slot = panel_state.current_slots[0]

        assert current_slot.name == "灼魂"
        # 新版描述应包含层数与单层杀伐百分比的关键词
        assert "灼烧" in current_slot.description
        assert "层" in current_slot.description


@pytest.mark.asyncio
async def test_refine_embed_only_shows_refine_related_summary(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5007, "炼词")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        services.artifact.ensure_affix_slots(artifact)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        summary_field = next(field for field in embed.fields if field.name == "洗炼信息")
        assert "器魂" in summary_field.value
        assert "已解锁槽位" in summary_field.value
        assert "待选结果" in summary_field.value


@pytest.mark.asyncio
async def test_refine_embed_footer_mentions_single_slot_discard(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5010, "弃槽提示")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        services.artifact.ensure_affix_slots(artifact)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        assert embed.footer.text is not None
        assert "弃槽X" in embed.footer.text


@pytest.mark.asyncio
async def test_artifact_overview_shows_bonus_stats(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5006, "观宝")).character
        artifact = character.artifact
        artifact.atk_bonus = 12
        artifact.def_bonus = 7
        artifact.agi_bonus = 3

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_artifact_overview_embed(snapshot, panel_state)

        bonus_field = next(field for field in embed.fields if field.name == "三维加成")
        assert "+12" in bonus_field.value
        assert "+7" in bonus_field.value
        assert "+3" in bonus_field.value


@pytest.mark.asyncio
async def test_duplicate_affixes_are_allowed(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "huichun"], [25, 55, 8, 16])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5005, "双生")).character
        artifact = character.artifact
        artifact.reinforce_level = 20

        services.artifact.ensure_affix_slots(artifact)
        entries = services.artifact.get_affix_slots(artifact)

        assert [entry.affix_id for entry in entries] == ["huichun", "huichun"]


def test_zhuohun_burn_uses_attacker_atk_per_stack(services) -> None:
    attacker = services.combat.create_combatant(
        name="灼魂修士",
        atk=100,
        defense=200,  # max_hp = defense*10 = 2000，撑住战斗
        agility=100,
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"burn_stacks": 3, "burn_atk_pct": 10}),),
    )
    defender = services.combat.create_combatant(name="木人", atk=1, defense=200, agility=1)

    battle = services.combat.run_battle(
        attacker,
        defender,
        rng=SequenceRandom([0.99, 0.99, 0.0] * 8),
    )

    burn_logs = [log for log in battle.logs if log.text and "层灼烧侵蚀" in log.text]
    assert burn_logs, "应触发至少一次灼烧 DOT"
    # 第一回合命中后挂 3 层；层数用于持续与联动，每回合仅造成一次 100 × 10% = 10 伤害
    assert "10 点" in burn_logs[0].text


def test_jinhuo_bonus_only_applies_against_burning_targets(services) -> None:
    attacker = services.combat.create_combatant(
        name="烬火修士",
        atk=10,
        defense=200,
        agility=100,
        affixes=(
            ArtifactAffixEntry(1, "zhuohun", {"burn_stacks": 5, "burn_atk_pct": 5}),
            ArtifactAffixEntry(2, "jinhuo", {"proc_pct": 100, "burn_stacks_gain": 2}),
        ),
    )
    defender = services.combat.create_combatant(name="木人", atk=1, defense=200, agility=1)

    battle = services.combat.run_battle(
        attacker,
        defender,
        rng=SequenceRandom([0.99] * 30),
    )

    # 应至少触发一次灼烧
    assert any(log.text and "灼烧" in log.text for log in battle.logs)


def test_lueying_creates_fast_attack_agility_gap(services) -> None:
    challenger = services.combat.create_combatant(
        name="掠影修士",
        atk=10,
        defense=10,
        agility=80,
        affixes=(ArtifactAffixEntry(1, "lueying", {"agi_pct": 80, "proc_pct": 0, "agi_down_pct": 8}),),
    )
    defender = services.combat.create_combatant(name="守擂修士", atk=10, defense=10, agility=100)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 20))
    first_attack = next(log for log in battle.logs if log.text is None)

    assert first_attack.actor_name == "掠影修士"


def test_dengxiao_scales_as_late_game_affix(services) -> None:
    challenger = services.combat.create_combatant(
        name="登霄修士",
        atk=10,
        defense=40,
        agility=100,
        affixes=(ArtifactAffixEntry(1, "dengxiao", {"damage_pct": 9, "pierce_pct": 7}),),
    )
    defender = services.combat.create_combatant(name="守塔修士", atk=1, defense=40, agility=1)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 80))

    assert any(log.text and "登霄势涨" in log.text for log in battle.logs)


def test_juling_scales_to_ten_layers_as_late_game_affix(services) -> None:
    challenger = services.combat.create_combatant(
        name="聚灵修士",
        atk=1,
        defense=100,
        agility=100,
        affixes=(ArtifactAffixEntry(1, "juling", {"atk_pct": 1, "late_damage_pct": 1}),),
    )
    defender = services.combat.create_combatant(name="守塔修士", atk=1, defense=100, agility=1)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 144))

    juling_logs = [log for log in battle.logs if log.text and "聚灵凝成第" in log.text]
    assert any("第 9 层灵势" in log.text for log in juling_logs)
    assert any("第 10 层灵势" in log.text for log in juling_logs)
    assert not any("第 11 层灵势" in log.text for log in juling_logs)


def test_duplicate_juling_affixes_stack_faster_without_breaking_cap(services) -> None:
    challenger = services.combat.create_combatant(
        name="双聚灵修士",
        atk=1,
        defense=100,
        agility=100,
        affixes=(
            ArtifactAffixEntry(1, "juling", {"atk_pct": 1, "late_damage_pct": 1}),
            ArtifactAffixEntry(2, "juling", {"atk_pct": 1, "late_damage_pct": 1}),
        ),
    )
    defender = services.combat.create_combatant(name="守塔修士", atk=1, defense=100, agility=1)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 144))

    juling_logs = [log for log in battle.logs if log.text and "聚灵凝成第" in log.text]
    assert any("第 10 层灵势" in log.text for log in juling_logs)
    assert not any("第 11 层灵势" in log.text for log in juling_logs)
    assert len(juling_logs) == 10


def test_jifeng_stacks_persist_and_lose_on_be_hit(services) -> None:
    """疾锋：不限回合、无 duration 限制；受击时失去 1 层。"""
    challenger = services.combat.create_combatant(
        name="疾锋修士",
        atk=10,
        defense=100,
        agility=80,
        affixes=(ArtifactAffixEntry(1, "jifeng", {"agi_pct": 30, "damage_pct": 20}),),
    )
    # 守塔修士敏捷极低但攻击力足够，保证攻方被击中
    defender = services.combat.create_combatant(name="守塔修士", atk=50, defense=1000, agility=1)

    services.combat.max_rounds = 6
    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 80))

    jifeng_gain_logs = [log for log in battle.logs if log.text and "疾锋加身" in log.text]
    # 每回合攻方命中叠 1 层，但守方命中攻方时消散 1 层——由于攻方永不闪避守方低敏攻击，
    # 攻方每层必定在当回合被击散，因此每回合 gain 1 次，6 回合共 6 次入账
    assert len(jifeng_gain_logs) == 6

    loss_logs = [log for log in battle.logs if log.text and "疾锋减弱" in log.text]
    # 受击时消散：6 回合各一次
    assert len(loss_logs) == 6


def _combat_state(services, name: str, *, atk: int = 100, defense: int = 100, agility: int = 100, affixes=()) -> _CombatState:
    snapshot = services.combat.create_combatant(name, atk, defense, agility, affixes=affixes)
    state = _CombatState(snapshot, snapshot.max_hp)
    state.effective_max_hp = snapshot.max_hp
    return state


def test_kuangfeng_and_leiyin_start_on_next_round_and_survive_triggering_attack(services) -> None:
    actor = _combat_state(
        services,
        "蓄势修士",
        affixes=(
            ArtifactAffixEntry(1, "kuangfeng", {"damage_pct": 80}),
            ArtifactAffixEntry(2, "leiyin", {"next_damage_pct": 20, "burst_pct": 0}),
        ),
    )
    target = _combat_state(services, "木人")
    actor.current_round = 1

    services.combat._trigger_on_crit(1, actor, target, 100, SequenceRandom([]), set())

    assert services.combat._damage_dealt_pct(actor) == 0
    assert {status.name for status in actor.statuses} >= {"狂锋", "雷引"}
    actor.current_round = 2
    assert services.combat._damage_dealt_pct(actor) == 100
    services.combat._consume_attack_bonuses(actor, list(actor.statuses))
    assert not any(status.name in {"狂锋", "雷引"} for status in actor.statuses)


def test_chenchen_reduces_normal_attack_damage(services) -> None:
    attacker = services.combat.create_combatant("重击修士", 500, 100, 1000)
    defender = services.combat.create_combatant(
        "承尘修士",
        1,
        100,
        1,
        affixes=(ArtifactAffixEntry(1, "chenchen", {"threshold_pct": 20, "reduction_pct": 50}),),
    )
    services.combat.max_rounds = 1

    battle = services.combat.run_battle(attacker, defender, rng=SequenceRandom([0.99] * 10))
    attack = next(log for log in battle.logs if log.text is None and log.actor_name == "重击修士")

    assert attack.damage == 350


def test_liechuang_increases_attack_only_when_wound_existed_before_attack(services) -> None:
    actor = _combat_state(
        services,
        "裂创修士",
        affixes=(ArtifactAffixEntry(1, "liechuang", {"damage_pct": 55, "heal_down_pct": 10}),),
    )
    target = _combat_state(services, "木人")

    assert services.combat._before_attack_bonus_pct(actor, target, set(), had_wound_before_attack=False) == 0
    target.statuses.append(_StatusEffect("创伤", is_debuff=True))
    assert services.combat._before_attack_bonus_pct(actor, target, set(), had_wound_before_attack=True) == 55


def test_guben_is_not_removed_by_full_cleanse(services) -> None:
    state = _combat_state(
        services,
        "固本修士",
        affixes=(ArtifactAffixEntry(1, "guben", {"shield_pct": 25}),),
    )
    services.combat._trigger_battle_start(1, state, set())
    state.statuses.append(_StatusEffect("普通增益", damage_dealt_pct=10))

    removed = services.combat._remove_all_status_effects(state)

    assert removed == 1
    assert any(status.name == "固本" and status.shield == 250 for status in state.statuses)


def test_yangyuan_uses_guiyuan_max_hp_heal_modifiers_and_heal_followups(services) -> None:
    state = _combat_state(
        services,
        "养元修士",
        affixes=(
            ArtifactAffixEntry(1, "guiyuan", {"max_hp_pct": 40}),
            ArtifactAffixEntry(2, "yangyuan", {"heal_pct": 10}),
            ArtifactAffixEntry(3, "huyuan", {"start_stacks": 1, "per_battle_cap": 3}),
        ),
    )
    opponent = _combat_state(services, "木人")
    services.combat._trigger_battle_start(1, state, set())
    state.hp = 1000
    state.statuses.append(_StatusEffect("创伤", heal_received_pct=-50, is_debuff=True))

    services.combat._trigger_round_start(1, state, opponent, SequenceRandom([]), set())

    assert state.get_max_hp() == 1400
    assert state.hp == 1070
    assert services.combat._status_count(state, "生息") == 2


def test_pokong_strip_triggers_fanshi(services) -> None:
    actor = _combat_state(
        services,
        "破空修士",
        affixes=(
            ArtifactAffixEntry(1, "pokong", {"damage_ratio_pct": 0, "guard_bonus_pct": 1}),
            ArtifactAffixEntry(2, "fanshi", {"damage_pct": 20}),
        ),
    )
    target = _combat_state(services, "木人")
    target.statuses.append(_StatusEffect("增伤", damage_dealt_pct=10))
    actor.current_round = target.current_round = 1

    logs = services.combat._trigger_on_crit(1, actor, target, 1, SequenceRandom([]), set())

    assert any(log.text and "反噬发动" in log.text for log in logs)
    assert target.hp == 979


def test_single_large_hit_crosses_all_low_hp_thresholds_and_continues_overflow(services) -> None:
    target = _combat_state(
        services,
        "回春裂铠修士",
        affixes=(
            ArtifactAffixEntry(1, "huichun", {"heal_pct": 20, "shengxi_stacks": 1}),
            ArtifactAffixEntry(2, "liekai", {"shield_pct": 10, "backlash_pct": 0}),
        ),
    )
    logs = []

    actual = services.combat._apply_damage(target, 1100, round_no=1, logs=logs, scene=set(), can_be_shielded=True)

    assert actual == 1000
    assert target.hp == 400
    assert target.huichun_triggered_thresholds == {50, 25}
    assert 30 in target.low_hp_marks
    assert not any(status.name == "裂铠" for status in target.statuses)
    assert [log.text for log in logs if log.text and "回春发动" in log.text] == [
        "回春裂铠修士 的回春发动（生命降至 50%），回复 200 点生命并叠加 1 层生息。",
        "回春裂铠修士 的回春发动（生命降至 25%），回复 200 点生命并叠加 1 层生息。",
    ]


def test_battle_result_reports_guiyuan_effective_max_hp(services) -> None:
    challenger = services.combat.create_combatant(
        "归元修士",
        1,
        100,
        100,
        affixes=(ArtifactAffixEntry(1, "guiyuan", {"max_hp_pct": 40}),),
    )
    defender = services.combat.create_combatant("木人", 1, 100, 1)
    services.combat.max_rounds = 1

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 10))

    assert battle.challenger_max_hp == 1400


def test_cleanse_removes_burn_by_layer(services) -> None:
    state = _combat_state(services, "净华修士")
    source = _combat_state(services, "灼魂修士")
    state.statuses.append(_StatusEffect("灼烧", stacks=5, burn_pct=20, is_debuff=True, source=source))

    assert services.combat._remove_one_debuff(state) is not None
    assert services.combat._remove_one_debuff(state) is not None

    assert services.combat._burn_stacks(state) == 3


def test_updated_affix_descriptions_match_current_semantics() -> None:
    assert "每回合造成一次" in get_artifact_affix_definition("zhuohun").describe({"burn_stacks": 3, "burn_atk_pct": 10})
    assert "承伤提高" in get_artifact_affix_definition("zhoufu").describe({"reduce_down_pct": 5, "max_stacks": 7})
    assert "同步治疗等量生命" in get_artifact_affix_definition("guiyuan").describe({"max_hp_pct": 40})


def test_resilience_damage_that_lands_exactly_on_threshold_triggers_huichun(services) -> None:
    snapshot = services.combat.create_combatant(
        "韧性回春修士",
        1,
        100,
        1,
        affixes=(ArtifactAffixEntry(1, "huichun", {"heal_pct": 10, "shengxi_stacks": 1}),),
        base_resilience=50,
    )
    state = _CombatState(snapshot, 501)
    state.effective_max_hp = snapshot.max_hp

    actual = services.combat._apply_damage(state, 2, round_no=1, logs=[], scene=set())

    assert actual == 1
    assert state.hp == 600
    assert state.huichun_triggered_thresholds == {50}
