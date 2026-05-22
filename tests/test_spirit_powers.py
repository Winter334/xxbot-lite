from __future__ import annotations

import json

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SPIRIT_POWER_DEFINITIONS, SpiritPowerEntry, get_spirit_power_definition


class CombatRoller:
    def __init__(self, random_values, *, fallback: float = 0.99) -> None:
        self._random_values = iter(random_values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._random_values, self._fallback)


def test_spirit_power_pool_expands_to_twenty_entries() -> None:
    power_ids = {definition.power_id for definition in SPIRIT_POWER_DEFINITIONS}

    assert len(SPIRIT_POWER_DEFINITIONS) == 24
    assert {"shisheng", "jueming", "xuanjia", "fanji", "guifeng", "niepan", "jinmai", "xuekuang"} <= power_ids
    assert {"fenmai", "luejie", "chengshi", "lingyong", "zhuying", "huajing", "duofeng"} <= power_ids
    assert {"chunsheng", "suijue", "dishi", "qiedao", "zhuifeng"} <= power_ids
    # 新增神通
    assert {"leifa", "shiyan", "fengdun", "lingyu"} <= power_ids


@pytest.mark.asyncio
async def test_existing_spirit_json_remains_compatible_after_pool_expansion(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 6101, "旧灵")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "旧灵"
        artifact.spirit_json = json.dumps(
            {
                "tier": "high",
                "stats": [
                    {"stat": "atk", "kind": "flat", "value": 3200},
                    {"stat": "def", "kind": "ratio", "value": 24},
                    {"stat": "agi", "kind": "ratio", "value": 18},
                ],
                "power": {"power_id": "niepan", "rolls": {"heal_pct": 42, "reduce_pct": 72}},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        snapshot = services.character.build_snapshot(creation.character)
        await session.commit()

        assert snapshot.spirit_name == "旧灵"
        assert snapshot.spirit_power_name == "涅槃"
        assert services.spirit.get_current_spirit(artifact) is not None


def test_spirit_power_description_accepts_legacy_rolls() -> None:
    # 旧 rolls 仅有 heal_pct，新描述需要兜底使用旧字段或默认值
    description = get_spirit_power_definition("niepan").describe({"heal_pct": 42})

    assert "生息" in description
    assert "复活" in description


def test_shisheng_can_heal_from_zhuohun_burn_damage(services) -> None:
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"burn_stacks": 3, "burn_atk_pct": 30})
    roller = CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99])

    attacker_without_spirit = services.combat.create_combatant(
        name="焚者",
        atk=85,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
    )
    attacker_with_spirit = services.combat.create_combatant(
        name="焚者",
        atk=85,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("shisheng", {"heal_pct": 100}),
    )
    defender = services.combat.create_combatant(name="枯木", atk=30, defense=400, agility=10)

    baseline = services.combat.run_battle(attacker_without_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker_with_spirit, defender, rng=roller)

    assert empowered.challenger_hp_after >= baseline.challenger_hp_after
    assert any(log.text and "噬生吞回血气" in log.text for log in empowered.logs)


def test_fenmai_triggers_extra_damage_on_burning_target(services) -> None:
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"burn_stacks": 3, "burn_atk_pct": 30})

    attacker_without_spirit = services.combat.create_combatant(
        name="烬心",
        atk=70,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
    )
    attacker_with_spirit = services.combat.create_combatant(
        name="烬心",
        atk=70,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("fenmai", {"cap_pct": 25}),
    )
    defender = services.combat.create_combatant(name="荒甲", atk=25, defense=400, agility=10)

    baseline = services.combat.run_battle(attacker_without_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker_with_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0]))

    # 焚脉提供额外伤害但不影响自身血量；以伤害日志/局数为准
    assert empowered.defender_hp_after <= baseline.defender_hp_after
    assert any(log.text and "焚脉" in log.text for log in empowered.logs)


def test_shiyan_consumes_burn_stacks_when_threshold_reached(services) -> None:
    """蚀焰：灼烧 ≥6 层时触发，引爆后清空灼烧并给目标挂创伤。"""
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"burn_stacks": 5, "burn_atk_pct": 20})
    attacker = services.combat.create_combatant(
        name="蚀焰主",
        atk=80,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("shiyan", {"per_burn_pct": 50, "wound_stacks": 3}),
    )
    defender = services.combat.create_combatant(name="木人", atk=10, defense=800, agility=10)

    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller([0.99] * 30))

    # 命中后挂 5 层即触发蚀焰
    assert any(log.text and "蚀焰引爆" in log.text for log in battle.logs)
    # 引爆后给目标附加创伤
    assert any(log.text and "创伤" in log.text for log in battle.logs)


def test_shiyan_explodes_even_when_attack_deals_zero_damage(services) -> None:
    """蚀焰：即使本次普攻被高防完全削为 0 伤害，仍应触发引爆并清空灼烧。"""
    # 两轮命中 → 10 层灼烧（≥6 触发）
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"burn_stacks": 5, "burn_atk_pct": 1})
    attacker = services.combat.create_combatant(
        name="蚀焰主",
        atk=10,           # 极低攻击
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("shiyan", {"per_burn_pct": 50, "wound_stacks": 2}),
    )
    # 极高防御 → 普攻被削到 0 伤
    defender = services.combat.create_combatant(name="铁壁", atk=10, defense=10_000_000, agility=10)

    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller([0.99] * 30))

    # 即使普攻 0 伤，蚀焰仍应触发并写入战报
    assert any(log.text and "蚀焰引爆" in log.text for log in battle.logs)


def test_shiyan_explosion_respects_damage_reduction(services) -> None:
    """蚀焰：引爆伤害吃减伤管线（吃承伤/减伤/护盾，不吃增伤）。

    2026-05-21 平衡调整：蚀焰 profile can_be_shielded 改为 True，护盾可抵挡引爆伤害。
    """
    import re
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"burn_stacks": 5, "burn_atk_pct": 20})

    def run_one(defender_affixes):
        attacker = services.combat.create_combatant(
            name="蚀焰主", atk=80, defense=10, agility=50,
            affixes=(burn_affix,),
            spirit_power=SpiritPowerEntry("shiyan", {"per_burn_pct": 50, "wound_stacks": 3}),
        )
        defender = services.combat.create_combatant(
            name="守势", atk=10, defense=800, agility=10, affixes=defender_affixes,
        )
        return services.combat.run_battle(attacker, defender, rng=CombatRoller([0.99] * 30))

    battle_no = run_one(())
    battle_red = run_one((ArtifactAffixEntry(slot=1, affix_id="cangbi", rolls={"reduce_pct": 80}),))

    explode_no = next((log for log in battle_no.logs if log.text and "蚀焰引爆" in log.text), None)
    explode_red = next((log for log in battle_red.logs if log.text and "蚀焰引爆" in log.text), None)
    assert explode_no is not None and explode_red is not None

    def extract_dmg(log):
        m = re.search(r"造成\s*([0-9]+)\s*点伤害", log.text)
        return int(m.group(1)) if m else None

    dmg_no = extract_dmg(explode_no)
    dmg_red = extract_dmg(explode_red)
    assert dmg_no is not None and dmg_red is not None, f"无法解析伤害: {explode_no.text} | {explode_red.text}"
    # 蚀焰引爆吃减伤：守势 80% 减伤后伤害应明显降低
    assert dmg_red < dmg_no, f"无减伤伤害 {dmg_no}, 守势减伤后 {dmg_red}（蚀焰应受减伤影响）"


def test_lingyong_grants_starting_lingshi_stacks(services) -> None:
    """灵涌：战斗开始即获得 start_stacks 层灵势，每层灵势提供 per_stack_pct% 增伤。"""
    attacker = services.combat.create_combatant(
        name="灵涌主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry("lingyong", {"start_stacks": 3, "per_stack_pct": 4}),
    )
    defender = services.combat.create_combatant(name="木人", atk=1, defense=100, agility=10)
    services.combat.max_rounds = 1
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller([0.99] * 10))

    # battle_start 应给攻方叠 3 层灵势（带灵涌器灵专属日志）
    assert any(log.text and "灵涌" in log.text and "3 层" in log.text for log in battle.logs)


def test_lingyu_provides_reduction_in_first_six_rounds_only(services) -> None:
    """灵御：前 6 回合每层灵势提供减伤；第 7 回合起效果消失。"""
    juling_affix = ArtifactAffixEntry(slot=1, affix_id="juling", rolls={"atk_pct": 1, "late_damage_pct": 1})
    # defense=1000 => hp=10000，能撑 7 回合验证灵御回合限制
    defender = services.combat.create_combatant(
        name="灵御主", atk=1, defense=1000, agility=10,
        affixes=(juling_affix,),
        spirit_power=SpiritPowerEntry("lingyu", {"reduce_per_stack_pct": 8, "self_damage_down_per_stack_pct": 8}),
    )
    attacker = services.combat.create_combatant(name="施压", atk=500, defense=10, agility=50)
    services.combat.max_rounds = 8
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller([0.99] * 60))

    # 战斗中应能持续到至少 6 回合（说明灵御保命有效）
    rounds_seen = max((log.round_no for log in battle.logs), default=0)
    assert rounds_seen >= 6, f"灵御应保命至少 6 回合，实际 {rounds_seen}"


def test_huajing_converts_reduction_affix_into_recovery(services) -> None:
    # 藏壁：每回合首次受击后获得守势（替代已移除的镇脉）
    # 压低攻方攻击确保守方第一回合存活，让藏壁能在第二回合生效
    reduce_affix = ArtifactAffixEntry(slot=1, affix_id="cangbi", rolls={"reduce_pct": 50})
    services.combat.max_rounds = 2
    attacker = services.combat.create_combatant(name="破锋", atk=60, defense=10, agility=40)
    defender_without_spirit = services.combat.create_combatant(
        name="守川",
        atk=20,
        defense=10,
        agility=10,
        affixes=(reduce_affix,),
    )
    defender_with_spirit = services.combat.create_combatant(
        name="守川",
        atk=20,
        defense=10,
        agility=10,
        affixes=(reduce_affix,),
        spirit_power=SpiritPowerEntry("huajing", {"convert_pct": 100}),
    )

    baseline = services.combat.run_battle(attacker, defender_without_spirit, rng=CombatRoller([0.99, 0.99, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker, defender_with_spirit, rng=CombatRoller([0.99, 0.99, 0.99, 0.99]))

    assert empowered.defender_hp_after > baseline.defender_hp_after
    assert any(log.text and "化劲" in log.text for log in empowered.logs)


# ---------------------------------------------------------------------------
# 器灵品阶淬炼 (upgrade_owned_spirit_tier)
# ---------------------------------------------------------------------------

_BASE_LOW_SPIRIT = {
    "tier": "low",
    "stats": [
        {"stat": "atk", "kind": "flat", "value": 1500},
        {"stat": "def", "kind": "ratio", "value": 12},
        {"stat": "agi", "kind": "ratio", "value": 8},
    ],
    "power": {"power_id": "niepan", "rolls": {"heal_pct": 30, "reduce_pct": 50}},
}

_BASE_SUPREME_SPIRIT = {
    "tier": "supreme",
    "stats": [
        {"stat": "atk", "kind": "flat", "value": 9000},
        {"stat": "def", "kind": "ratio", "value": 50},
        {"stat": "agi", "kind": "ratio", "value": 50},
    ],
    "power": {"power_id": "niepan", "rolls": {"heal_pct": 90, "reduce_pct": 99}},
}


def _dump_spirit(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.asyncio
async def test_upgrade_tier_blocked_without_owned_spirit(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 7001, "测一")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = None
        artifact.spirit_json = None
        artifact.soul_shards = 1000

        result = services.spirit.upgrade_owned_spirit_tier(artifact)

        assert result.success is False
        assert artifact.soul_shards == 1000
        assert services.spirit.get_current_spirit(artifact) is None


@pytest.mark.asyncio
async def test_upgrade_tier_blocked_when_supreme(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 7002, "测二")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "已绝"
        artifact.spirit_json = _dump_spirit(_BASE_SUPREME_SPIRIT)
        artifact.soul_shards = 5000

        result = services.spirit.upgrade_owned_spirit_tier(artifact)

        assert result.success is False
        assert artifact.soul_shards == 5000
        assert services.spirit.get_current_spirit(artifact).tier == "supreme"


@pytest.mark.asyncio
async def test_upgrade_tier_blocked_by_insufficient_soul(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 7003, "测三")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "穷酸"
        artifact.spirit_json = _dump_spirit(_BASE_LOW_SPIRIT)
        artifact.soul_shards = 10  # < 80

        result = services.spirit.upgrade_owned_spirit_tier(artifact)

        assert result.success is False
        assert artifact.soul_shards == 10
        assert services.spirit.get_current_spirit(artifact).tier == "low"


@pytest.mark.asyncio
async def test_upgrade_tier_blocked_by_pending_spirit(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 7004, "测四")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "有挂"
        artifact.spirit_json = _dump_spirit(_BASE_LOW_SPIRIT)
        artifact.spirit_pending_json = _dump_spirit(_BASE_LOW_SPIRIT)
        artifact.soul_shards = 1000

        result = services.spirit.upgrade_owned_spirit_tier(artifact)

        assert result.success is False
        assert artifact.soul_shards == 1000
        assert services.spirit.get_current_spirit(artifact).tier == "low"


@pytest.mark.asyncio
async def test_upgrade_tier_success_low_to_mid(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 7005, "测五")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "成长"
        artifact.spirit_json = _dump_spirit(_BASE_LOW_SPIRIT)
        artifact.soul_shards = 200

        result = services.spirit.upgrade_owned_spirit_tier(artifact)

        assert result.success is True
        assert result.tier_before == "low"
        assert result.tier_after == "mid"
        assert result.soul_cost == 80
        assert artifact.soul_shards == 120
        assert services.spirit.get_current_spirit(artifact).tier == "mid"
