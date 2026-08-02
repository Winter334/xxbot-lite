from __future__ import annotations

import json
import random

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SPIRIT_POWER_DEFINITIONS, SpiritPowerEntry, get_spirit_power_definition
from bot.services.combat_service import _CombatState, _DamageSource, _StatusEffect


class CombatRoller:
    def __init__(self, random_values, *, fallback: float = 0.99) -> None:
        self._random_values = iter(random_values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._random_values, self._fallback)


def _spirit_state(services, name: str, *, atk: int = 100, defense: int = 100, agility: int = 100, affixes=(), spirit_power=None) -> _CombatState:
    snapshot = services.combat.create_combatant(
        name,
        atk,
        defense,
        agility,
        affixes=affixes,
        spirit_power=spirit_power,
    )
    state = _CombatState(snapshot, snapshot.max_hp)
    state.effective_max_hp = snapshot.max_hp
    return state


def test_statuses_merge_only_when_all_stack_properties_match(services) -> None:
    combat = services.combat
    owner = _spirit_state(services, "承载者")
    source = _spirit_state(services, "同源")
    other_source = _spirit_state(services, "异源")

    combat._add_status(owner, _StatusEffect("生息"))
    combat._add_status(owner, _StatusEffect("生息"))
    combat._add_status(owner, _StatusEffect("创伤", damage_taken_pct=5, is_debuff=True, source=source))
    combat._add_status(owner, _StatusEffect("创伤", damage_taken_pct=5, is_debuff=True, source=source))
    combat._add_status(owner, _StatusEffect("创伤", damage_taken_pct=8, is_debuff=True, source=source))
    combat._add_status(owner, _StatusEffect("创伤", damage_taken_pct=5, is_debuff=True, source=other_source))
    combat._add_status(owner, _StatusEffect("限时", duration=2))
    combat._add_status(owner, _StatusEffect("限时", duration=2))
    combat._add_status(owner, _StatusEffect("一次", remaining_hits=1, damage_dealt_pct=10))
    combat._add_status(owner, _StatusEffect("一次", remaining_hits=1, damage_dealt_pct=10))
    combat._add_status(owner, _StatusEffect("护盾", shield=100))
    combat._add_status(owner, _StatusEffect("护盾", shield=100))

    assert [s.stacks for s in owner.statuses if s.name == "生息"] == [2]
    assert [s.stacks for s in owner.statuses if s.name == "创伤"] == [2, 1, 1]
    assert len([s for s in owner.statuses if s.name == "限时"]) == 2
    assert len([s for s in owner.statuses if s.name == "一次"]) == 2
    assert len([s for s in owner.statuses if s.name == "护盾"]) == 2
    assert combat._status_count(owner, "限时") == 2
    assert combat._status_count(owner, "一次") == 2


def test_burn_uses_stacks_consumes_one_per_round_and_cleanses_one(services) -> None:
    combat = services.combat
    source = _spirit_state(services, "焚者", atk=100)
    stronger_source = _spirit_state(services, "烈焰者", atk=100)
    weaker_source = _spirit_state(services, "余火者", atk=100)
    target = _spirit_state(services, "木人")
    logs = []
    combat._apply_burn_to_target(target, source, stacks=3, per_stack_pct=20, round_no=1, logs=logs)
    combat._apply_burn_to_target(target, source, stacks=2, per_stack_pct=20, round_no=1, logs=logs)
    combat._apply_burn_to_target(target, stronger_source, stacks=1, per_stack_pct=30, round_no=1, logs=logs)
    combat._apply_burn_to_target(target, weaker_source, stacks=1, per_stack_pct=10, round_no=1, logs=logs)

    burns = [s for s in target.statuses if s.name == "灼烧"]
    assert len(burns) == 1
    assert burns[0].stacks == 7 and burns[0].duration is None
    assert burns[0].burn_pct == 30 and burns[0].source is stronger_source
    round_logs = combat._trigger_round_end(1, source, target, CombatRoller([]))
    assert len([log for log in round_logs if log.text and "层灼烧侵蚀" in log.text]) == 1
    assert combat._burn_stacks(target) == 6
    assert combat._remove_one_debuff(target) is not None
    assert combat._burn_stacks(target) == 5


@pytest.mark.parametrize(
    ("affix_ids", "expected"),
    [
        (("zhuiming",), 0),
        (("zhuiming", "zhuiming"), 40),
        (("zhuiming", "zhuiming", "duanyue"), 55),
    ],
)
def test_chengshi_counts_each_damage_affix_entry(services, affix_ids, expected) -> None:
    affixes = tuple(ArtifactAffixEntry(index, affix_id, {}) for index, affix_id in enumerate(affix_ids, 1))
    actor = _spirit_state(
        services,
        "乘势主",
        affixes=affixes,
        spirit_power=SpiritPowerEntry("chengshi", {"base_pct": 40, "per_type_pct": 15}),
    )
    target = _spirit_state(services, "木人")

    assert services.combat._spirit_damage_bonus_pct(actor, target) == expected


def test_chunsheng_increases_healing_received(services) -> None:
    state = _spirit_state(
        services,
        "春生主",
        spirit_power=SpiritPowerEntry("chunsheng", {"heal_received_pct": 50, "convert_pct": 0, "heal_shengxi_bonus": 0}),
    )
    state.hp = 500

    assert services.combat._heal(state, 20) == 300


def test_luejie_uses_debuff_stacks_for_bonus_and_followup(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "戮厄主",
        atk=100,
        spirit_power=SpiritPowerEntry("luejie", {"per_debuff_pct": 10, "max_bonus_pct": 200}),
    )
    target = _spirit_state(services, "木人", defense=1000)
    combat._add_status(target, _StatusEffect("创伤", stacks=5, is_debuff=True, source=actor))

    assert combat._spirit_damage_bonus_pct(actor, target) == 50
    logs = combat._resolve_action(1, actor, target, CombatRoller([0.99, 0.99]), set())
    followup = next(log for log in logs if log.text and "戮厄锁定 5 层负面" in log.text)
    assert "追加 10 点伤害" in followup.text


def test_duanyue_counts_debuff_objects_but_luejie_counts_layers(services) -> None:
    combat = services.combat
    target = _spirit_state(services, "木人")
    source = _spirit_state(services, "施术者")
    combat._add_status(target, _StatusEffect("灼烧", stacks=10, burn_pct=20, is_debuff=True, source=source))
    combat._add_status(target, _StatusEffect("创伤", stacks=3, is_debuff=True, source=source))
    duanyue = _spirit_state(
        services,
        "断岳主",
        affixes=(ArtifactAffixEntry(1, "duanyue", {"per_debuff_pct": 10, "max_bonus_pct": 100}),),
    )
    luejie = _spirit_state(
        services,
        "戮厄主",
        spirit_power=SpiritPowerEntry("luejie", {"per_debuff_pct": 10, "max_bonus_pct": 200}),
    )

    assert combat._before_attack_bonus_pct(duanyue, target, set()) == 20
    assert combat._spirit_damage_bonus_pct(luejie, target) == 130


def test_fengren_grants_same_attack_guaranteed_crit_and_fifty_pct_damage(services) -> None:
    combat = services.combat
    actor = _spirit_state(services, "风刃主", atk=100)
    target = _spirit_state(services, "木人")
    combat._add_status(actor, _StatusEffect("风刃", guarantee_crit=True, damage_dealt_pct=50, remaining_hits=1))

    logs = combat._resolve_action(1, actor, target, CombatRoller([0.99]), set())
    attack = next(log for log in logs if log.text is None)

    assert attack.critical is True
    assert attack.damage == 262
    assert not any(s.name == "风刃" for s in actor.statuses)


def test_cleaning_one_zhoufu_stack_does_not_add_curse_seal(services) -> None:
    combat = services.combat
    owner = _spirit_state(services, "咒缚主")
    target = _spirit_state(services, "受咒者")
    combat._add_status(target, _StatusEffect("咒缚", stacks=2, damage_taken_pct=5, is_debuff=True, source=owner))

    assert combat._remove_one_debuff(target) is not None
    combat._trigger_cleanse_followups(1, target, 1, owner)

    assert combat._status_count(target, "咒缚") == 1
    assert combat._curse_seal_count(target) == 0


def test_dishi_counts_only_explicit_stacks_and_keeps_uncleanseable(services) -> None:
    combat = services.combat
    owner = _spirit_state(
        services,
        "涤世主",
        spirit_power=SpiritPowerEntry("dishi", {"threshold": 1, "stack_pct": 10}),
    )
    opponent = _spirit_state(services, "对手")
    owner.statuses.extend(
        [
            _StatusEffect("甲", stacks=10, duration=50),
            _StatusEffect("不可净化", stacks=10, cleanseable=False),
        ]
    )
    opponent.statuses.extend(
        [
            _StatusEffect("乙", stacks=10, remaining_hits=7),
            _StatusEffect("丙", stacks=10, is_debuff=True),
            _StatusEffect("丁", stacks=10),
        ]
    )

    logs = combat._trigger_spirit_round_end(1, owner, opponent, CombatRoller([]))

    assert any(log.text and "共 40 层效果" in log.text for log in logs)
    followup = next(s for s in owner.statuses if s.name == "涤世·净化")
    assert followup.bonus_damage == 400
    assert any(s.name == "不可净化" and s.stacks == 10 for s in owner.statuses)


def test_niepan_revives_after_huanbu_dodge_counter_in_run_battle(services) -> None:
    niepan = SpiritPowerEntry(
        "niepan",
        {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0, "revive_shield_pct": 0},
    )
    attacker = services.combat.create_combatant(
        "涅槃者",
        10,
        10,
        1,
        affixes=(ArtifactAffixEntry(1, "huyuan", {"start_stacks": 1, "per_battle_cap": 0}),),
        spirit_power=niepan,
    )
    dodger = services.combat.create_combatant(
        "幻步者",
        100,
        100,
        1000,
        affixes=(ArtifactAffixEntry(1, "huanbu", {"dodge_pct": 100, "counter_pct": 1000}),),
    )
    services.combat.max_rounds = 5

    battle = services.combat.run_battle(attacker, dodger, rng=CombatRoller([0.0] * 40, fallback=0.0))

    assert any(log.text and "幻步虚影" in log.text for log in battle.logs)
    assert any(log.text and "涅槃再起" in log.text for log in battle.logs)


def test_niepan_revives_after_jueming_at_round_end(services, monkeypatch) -> None:
    combat = services.combat
    original_battle_start = combat._trigger_battle_start

    def battle_start(round_no, state, scene):
        logs = original_battle_start(round_no, state, scene)
        if state.snapshot.name == "涅槃者":
            combat._add_status(state, _StatusEffect("生息"))
            combat._add_status(
                state,
                _StatusEffect("死兆", is_debuff=True, source=executioner_state[0], cleanseable=False),
            )
        return logs

    executioner_state = [None]
    monkeypatch.setattr(combat, "_trigger_battle_start", battle_start)
    victim = combat.create_combatant(
        "涅槃者",
        1,
        100,
        1,
        spirit_power=SpiritPowerEntry(
            "niepan",
            {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0, "revive_shield_pct": 0},
        ),
    )
    executioner = combat.create_combatant(
        "绝命主",
        1,
        100,
        100,
        spirit_power=SpiritPowerEntry("jueming", {"omen_cost": 99, "execute_pct": 100, "heal_down_pct": 0}),
    )
    executioner_state[0] = _spirit_state(
        services,
        "绝命标记源",
        spirit_power=executioner.spirit_power,
    )
    combat.max_rounds = 1

    battle = combat.run_battle(victim, executioner, rng=CombatRoller([0.99] * 10))

    assert any(log.text and "绝命发动" in log.text for log in battle.logs)
    assert any(log.text and "涅槃再起" in log.text for log in battle.logs)
    assert battle.challenger_hp_after > 0


def test_niepan_revives_after_chunsheng_followup_before_battle_result(services, monkeypatch) -> None:
    combat = services.combat
    original_battle_start = combat._trigger_battle_start

    def battle_start(round_no, state, scene):
        logs = original_battle_start(round_no, state, scene)
        if state.snapshot.name == "追打者":
            combat._add_status(state, _StatusEffect("春生·追击", bonus_damage=200, remaining_hits=1))
        elif state.snapshot.name == "涅槃者":
            combat._add_status(state, _StatusEffect("生息"))
        return logs

    monkeypatch.setattr(combat, "_trigger_battle_start", battle_start)
    attacker = combat.create_combatant("追打者", 1, 100, 100)
    victim = combat.create_combatant(
        "涅槃者",
        1,
        10,
        1,
        spirit_power=SpiritPowerEntry(
            "niepan",
            {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0, "revive_shield_pct": 0},
        ),
    )
    combat.max_rounds = 1

    battle = combat.run_battle(attacker, victim, rng=CombatRoller([0.99] * 10))

    followup_index = next(i for i, log in enumerate(battle.logs) if log.text and "春生回返一击" in log.text)
    revive_index = next(i for i, log in enumerate(battle.logs) if log.text and "涅槃再起" in log.text)
    assert followup_index < revive_index
    assert battle.defender_hp_after > 0


def test_spirit_power_pool_expands_to_twenty_entries() -> None:
    power_ids = {definition.power_id for definition in SPIRIT_POWER_DEFINITIONS}

    assert len(SPIRIT_POWER_DEFINITIONS) == 25
    assert {"shisheng", "jueming", "xuanjia", "fanji", "guifeng", "niepan", "jinmai", "xuekuang"} <= power_ids
    assert {"fenmai", "luejie", "chengshi", "lingyong", "zhuying", "huajing", "duofeng"} <= power_ids
    assert {"chunsheng", "suijue", "dishi", "qiedao", "zhuifeng"} <= power_ids
    # 新增神通
    assert {"leifa", "shiyan", "fengdun", "lingyu", "wanzhou"} <= power_ids


def test_fenmai_power_roll_accepts_decimal_ranges() -> None:
    power = get_spirit_power_definition("fenmai")

    entry = power.roll("high", random.Random(42))

    assert 1.2 <= entry.rolls["per_burn_pct"] <= 1.6
    assert isinstance(entry.rolls["per_burn_pct"], float)


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
    assert any(log.text and "蚀焰倾泻而出" in log.text for log in battle.logs)
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
    assert any(log.text and "蚀焰倾泻而出" in log.text for log in battle.logs)


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

    explode_no = next((log for log in battle_no.logs if log.text and "蚀焰倾泻而出" in log.text), None)
    explode_red = next((log for log in battle_red.logs if log.text and "蚀焰倾泻而出" in log.text), None)
    assert explode_no is not None and explode_red is not None

    def extract_dmg(log):
        m = re.search(r"承受\s*([0-9]+)\s*点焚伤", log.text)
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



def test_jueming_converts_curse_seals_to_death_omen_and_executes(services) -> None:
    combat = services.combat
    owner_snapshot = combat.create_combatant(
        name="绝命主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry("jueming", {"omen_cost": 2, "execute_pct": 50, "heal_down_pct": 40}),
    )
    target_snapshot = combat.create_combatant(name="受印者", atk=10, defense=100, agility=10)
    owner = _CombatState(owner_snapshot, owner_snapshot.max_hp)
    owner.effective_max_hp = owner_snapshot.max_hp
    target = _CombatState(target_snapshot, target_snapshot.max_hp)
    target.effective_max_hp = target_snapshot.max_hp

    combat._add_curse_seal(target, owner, 2)
    logs = combat._settle_jueming_marks(1, owner, target)

    assert combat._curse_seal_count(target) == 0
    assert combat._death_omen_count(target) == 1
    assert any(log.text and "死兆" in log.text for log in logs)

    target.hp = target.get_max_hp() * 40 // 100
    combat._settle_jueming_marks(2, owner, target)

    assert target.hp == 0


def test_wanzhou_bursts_curse_seals_into_debuffs(services) -> None:
    combat = services.combat
    actor_snapshot = combat.create_combatant(
        name="万咒主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry("wanzhou", {"curse_on_hit": 3, "extra_curse_pct": 0, "burst_threshold": 3, "debuff_rolls_per_curse": 2, "seal_weight": 12}),
    )
    target_snapshot = combat.create_combatant(name="靶子", atk=10, defense=100, agility=10)
    actor = _CombatState(actor_snapshot, actor_snapshot.max_hp)
    actor.effective_max_hp = actor_snapshot.max_hp
    target = _CombatState(target_snapshot, target_snapshot.max_hp)
    target.effective_max_hp = target_snapshot.max_hp

    logs = combat._trigger_spirit_on_hit(
        1, actor, target, 1, CombatRoller([0.0] * 20),
        source=_DamageSource.ATTACK, scene=set(),
    )

    assert combat._curse_seal_count(target) == 0
    assert combat._debuff_count(target) > 0
    assert any(log.text and "万咒" in log.text for log in logs)


def test_jinmai_action_seal_triggers_break_effect(services) -> None:
    combat = services.combat
    actor_snapshot = combat.create_combatant(
        name="禁脉主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry(
            "jinmai",
            {"proc_pct": 100, "per_disrupt_pct": 10, "seal_stacks": 2, "break_pobu_stacks": 2, "break_wound_stacks": 1, "break_strip_stacks": 0},
        ),
    )
    target_snapshot = combat.create_combatant(name="靶子", atk=10, defense=100, agility=10)
    actor = _CombatState(actor_snapshot, actor_snapshot.max_hp)
    actor.effective_max_hp = actor_snapshot.max_hp
    target = _CombatState(target_snapshot, target_snapshot.max_hp)
    target.effective_max_hp = target_snapshot.max_hp

    combat._trigger_spirit_on_hit(1, actor, target, 1, CombatRoller([0.0]), source=_DamageSource.ATTACK, scene=set())
    assert combat._status_count(target, "封禁行动") == 2

    logs, can_act = combat._trigger_before_action(1, target, actor)

    assert can_act is False
    assert combat._status_count(target, "封禁行动") == 1
    assert combat._status_count(target, "破步") >= 2
    assert combat._status_count(target, "创伤") >= 1
    assert any(log.text and "断脉" in log.text for log in logs)


def test_leifa_charges_on_noncrit_and_judges_on_marks(services) -> None:
    combat = services.combat
    actor_snapshot = combat.create_combatant(
        name="雷罚主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry(
            "leifa",
            {
                "crit_damage_base_pct": 50,
                "charge_crit_pct": 20,
                "mark_crit_pct": 15,
                "mark_crit_damage_pct": 20,
                "thunder_pct": 100,
                "mark_damage_pct": 10,
                "judgment_pct": 10,
                "wound_stacks": 2,
                "strip_stacks": 0,
                "crit_mark_stacks": 3,
                "retain_mark_after_judgment": 0,
            },
        ),
    )
    target_snapshot = combat.create_combatant(name="靶子", atk=10, defense=100, agility=10)
    actor = _CombatState(actor_snapshot, actor_snapshot.max_hp)
    actor.effective_max_hp = actor_snapshot.max_hp
    target = _CombatState(target_snapshot, target_snapshot.max_hp)
    target.effective_max_hp = target_snapshot.max_hp

    combat._trigger_spirit_on_noncrit(1, actor)
    assert combat._status_count(actor, "引雷") == 1

    logs = combat._trigger_spirit_on_crit(1, actor, target, 100, CombatRoller([0.99]))

    assert combat._status_count(actor, "引雷") == 0
    assert combat._status_count(target, "创伤") >= 2
    assert any(log.text and "天劫" in log.text for log in logs)


def test_zhuifeng_first_round_force_hit_and_crit(services) -> None:
    combat = services.combat
    actor_snapshot = combat.create_combatant(
        name="追风主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry(
            "zhuifeng",
            {"r1_damage_pct": 360, "r23_damage_pct": 210, "r1_crit_bonus": 100, "r23_crit_bonus": 50, "r1_agility_pct": 50, "r23_agility_pct": 32, "shield_damage_pct": 150, "r1_pierce_pct": 50, "r23_pierce_pct": 25, "hit_heal_down_pct": 55, "crit_heal_down_pct": 75, "chase_damage_pct": 16},
        ),
    )
    actor = _CombatState(actor_snapshot, actor_snapshot.max_hp)
    actor.effective_max_hp = actor_snapshot.max_hp
    actor.is_first_mover = True
    actor.current_round = 1

    assert combat._zhuifeng_force_hit(actor) is True
    assert combat._zhuifeng_force_crit(actor) is True
    assert combat._zhuifeng_crit_bonus_pct(actor) == 100
