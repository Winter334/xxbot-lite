from __future__ import annotations

import json
import random

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SPIRIT_POWER_DEFINITIONS, SpiritPowerEntry, get_spirit_power_definition
from bot.models.proving_ground_run import ProvingGroundRun
from bot.services.combat_service import _BURN_DOT_PROFILE, _CombatState, _DamageSource, _NORMAL_DAMAGE_PROFILE, _StatusEffect
from bot.services.proving_ground_service import PGBuild, ProvingGroundService


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


def test_burn_ticks_once_per_stack_without_consuming_and_cleanses_one(services) -> None:
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
    burn_logs = [log for log in round_logs if log.text and "层灼烧侵蚀" in log.text]
    assert len(burn_logs) == 1
    assert burn_logs[0].damage == 210
    assert combat._burn_stacks(target) == 7
    assert combat._remove_one_debuff(target) is not None
    assert combat._burn_stacks(target) == 6


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
        spirit_power=SpiritPowerEntry("chunsheng", {"heal_received_pct": 50, "convert_pct": 0}),
    )
    state.hp = 500

    assert services.combat._heal(state, 20) == 300


def test_niepan_stacks_shengxi_on_heal(services) -> None:
    combat = services.combat
    state = _spirit_state(
        services,
        "涅槃主",
        spirit_power=SpiritPowerEntry(
            "niepan", {"cost_stacks": 6, "revive_hp_pct": 50, "heal_shengxi_bonus": 2}
        ),
    )
    state.hp = 500

    assert combat._heal(state, 20) == 200
    assert combat._status_count(state, "生息") == 2


def test_chunsheng_heal_no_longer_stacks_shengxi(services) -> None:
    combat = services.combat
    state = _spirit_state(
        services,
        "春生主",
        spirit_power=SpiritPowerEntry("chunsheng", {"heal_received_pct": 0, "convert_pct": 0}),
    )
    state.hp = 500

    assert combat._heal(state, 20) == 200
    assert combat._status_count(state, "生息") == 0


def test_niepan_revive_no_longer_grants_shield(services) -> None:
    combat = services.combat
    state = _spirit_state(
        services,
        "涅槃主",
        spirit_power=SpiritPowerEntry(
            "niepan",
            {"cost_stacks": 2, "revive_hp_pct": 50, "per_revive_atk_pct": 10, "per_revive_speed_pct": 5, "heal_shengxi_bonus": 1},
        ),
    )
    state.hp = 0
    combat._add_status(state, _StatusEffect("生息"))
    combat._add_status(state, _StatusEffect("生息"))

    logs = combat._trigger_spirit_revive(1, state)

    assert state.hp > 0
    assert combat._status_count(state, "生息") == 0
    assert combat._status_count(state, "涅槃·余烬护盾") == 0
    assert not any(log.text and "护盾" in log.text for log in logs)


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
    assert followup.damage > 0
    assert "余血" in followup.text


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


def test_dodge_rate_caps_at_eighty_percent(services) -> None:
    combat = services.combat
    defender = _spirit_state(services, "守方", agility=1000)
    attacker = _spirit_state(services, "攻方", agility=1)

    assert combat._dodge_rate(defender, attacker) == pytest.approx(0.80)


def test_fengdun_starts_with_ten_percent_dodge(services) -> None:
    combat = services.combat
    dodger = _spirit_state(
        services,
        "风遁主",
        agility=100,
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 20, "agi_boost_pct": 10}),
    )
    attacker = _spirit_state(services, "来犯", agility=100)

    logs = combat._trigger_spirit_battle_start(1, dodger)

    assert combat._dodge_rate(dodger, attacker) == pytest.approx(0.20)
    assert combat._status_count(dodger, "风遁·起") == 1
    assert any(log.text and "闪避率提高 10%" in log.text for log in logs)


def test_fengdun_evades_non_attack_damage_and_gains_a_stack(services) -> None:
    combat = services.combat
    dodger = _spirit_state(
        services,
        "风遁主",
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 20, "agi_boost_pct": 10}),
    )
    dodger.roller = CombatRoller([0.0])
    attacker = _spirit_state(services, "来犯")
    logs: list = []

    actual = combat._apply_typed_damage(
        dodger,
        400,
        _BURN_DOT_PROFILE,
        actor=attacker,
        round_no=1,
        logs=logs,
    )

    assert actual == 0
    assert combat._status_count(dodger, "风遁") == 1
    assert dodger.hp == dodger.get_max_hp()
    dodge_logs = [log.text for log in logs if log.text and "以风遁闪开本次伤害" in log.text]
    assert dodge_logs == ["风遁主 以风遁闪开本次伤害，叠至第 1 层，攻势与身法同涨。"]
    assert not any(log.text and "风遁叠至第" in log.text for log in logs)


def test_fengdun_missed_non_attack_damage_drops_one_stack(services) -> None:
    combat = services.combat
    dodger = _spirit_state(
        services,
        "风遁主",
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 20, "agi_boost_pct": 10}),
    )
    combat._add_status(dodger, _StatusEffect("风遁", stacks=3, damage_dealt_pct=20, agility_pct=10))
    dodger.roller = CombatRoller([0.99])
    attacker = _spirit_state(services, "来犯")

    actual = combat._apply_typed_damage(dodger, 50, _NORMAL_DAMAGE_PROFILE, actor=attacker, round_no=1, logs=[])

    assert actual > 0
    assert combat._status_count(dodger, "风遁") == 2


def test_fengdun_normal_attack_does_not_double_dodge(services) -> None:
    combat = services.combat
    actor = _spirit_state(services, "攻方", atk=100, agility=100)
    target = _spirit_state(
        services,
        "风遁主",
        agility=100,
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 20, "agi_boost_pct": 10}),
    )
    target.roller = CombatRoller([])

    logs = combat._resolve_action(1, actor, target, CombatRoller([0.99, 0.99]), set())
    attack = next(log for log in logs if log.text is None)

    assert attack.dodged is False
    assert attack.damage > 0
    assert combat._status_count(target, "风遁") == 0


def test_fengdun_caps_at_ten_stacks(services) -> None:
    combat = services.combat
    dodger = _spirit_state(
        services,
        "风遁主",
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 20, "agi_boost_pct": 10}),
    )
    combat._add_status(dodger, _StatusEffect("风遁", stacks=10, damage_dealt_pct=20, agility_pct=10))

    logs = combat._trigger_spirit_on_dodge(1, dodger)

    assert logs == []
    assert combat._status_count(dodger, "风遁") == 10
    assert combat._status_stack_cap("风遁") == 10


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
        {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0},
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
            combat._add_curse_seal(state, executioner_state[0], 1)
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
            {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0},
        ),
    )
    executioner = combat.create_combatant(
        "绝命主",
        1,
        100,
        100,
        spirit_power=SpiritPowerEntry("jueming", {"omen_cost": 1, "hp_pct": 100, "heal_down_pct": 0}),
    )
    executioner_state[0] = _spirit_state(
        services,
        "绝命标记源",
        spirit_power=executioner.spirit_power,
    )
    combat.max_rounds = 1

    battle = combat.run_battle(victim, executioner, rng=CombatRoller([0.99] * 10))

    assert any(log.text and "凝成第 1 层死兆" in log.text for log in battle.logs)
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
            {"cost_stacks": 1, "revive_hp_pct": 50, "per_revive_atk_pct": 0, "per_revive_speed_pct": 0},
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


def test_fenmai_power_roll_accepts_integer_burn_stacks() -> None:
    power = get_spirit_power_definition("fenmai")

    entry = power.roll("high", random.Random(42))

    assert 2 <= entry.rolls["burn_stacks"] <= 3
    assert isinstance(entry.rolls["burn_stacks"], int)


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


@pytest.mark.parametrize(
    ("power_id", "rolls", "expected"),
    [
        ("xuanjia", {"proc_pct": 60, "reduce_pct": 100}, {"def_pct": 100, "proc_pct": 55, "heal_down_pct": 50}),
        ("jinmai", {"proc_pct": 85, "per_disrupt_pct": 10, "seal_stacks": 3}, {"proc_pct": 35, "per_disrupt_pct": 5}),
        ("zhuifeng", {"r1_crit_bonus": 100, "r1_agility_pct": 50, "r1_damage_pct": 480}, {"r1_crit_bonus": 50, "r1_agility_pct": 25}),
        ("leifa", {"mark_crit_pct": 15, "mark_crit_damage_pct": 20, "thunder_pct": 450}, {"cost_stacks": 3, "strikes_min": 3, "strikes_max": 6, "burst_pct": 60}),
        (
            "wanzhou",
            {"curse_on_hit": 3, "extra_curse_pct": 0, "burst_threshold": 5, "debuff_rolls_per_curse": 6, "seal_weight": 12},
            {"curse_on_hit": 3, "extra_curse_pct": 0, "burst_threshold": 5, "debuff_rolls_per_curse": 6},
        ),
        ("qiedao", {"chain_pct": 80}, {"chain_pct": 60}),
        ("shisheng", {"heal_pct": 75}, {"heal_pct": 60}),
        ("shisheng", {"heal_pct": 1}, {"heal_pct": 50}),
        ("xuekuang", {"per_lost_10_pct": 22, "max_bonus_pct": 180, "frenzy_lifesteal_pct": 28}, {"burn_pct": 5, "loss_step_pct": 10, "stat_pct": 5}),
        ("jueming", {"max_stacks": 6, "damage_pct": 55}, {"omen_cost": 5, "hp_pct": 35, "heal_down_pct": 20}),
        ("jueming", {"omen_cost": 4, "execute_pct": 35, "heal_down_pct": 55}, {"omen_cost": 5, "hp_pct": 35, "heal_down_pct": 25}),
    ],
)
def test_reworked_legacy_spirit_rolls_are_normalized(services, power_id, rolls, expected) -> None:
    spirit = services.spirit._load_spirit(
        json.dumps(
            {
                "tier": "supreme",
                "stats": [
                    {"stat": "atk", "kind": "flat", "value": 1},
                    {"stat": "def", "kind": "flat", "value": 1},
                    {"stat": "agi", "kind": "flat", "value": 1},
                ],
                "power": {"power_id": power_id, "rolls": rolls},
            }
        )
    )

    assert spirit is not None
    assert spirit.power.rolls == expected


def test_legacy_proving_ground_spirits_use_current_rolls() -> None:
    xuanjia = PGBuild.from_dict(
        {
            "spirit_tier": "supreme",
            "spirit_power": {"power_id": "xuanjia", "rolls": {"proc_pct": 60, "reduce_pct": 100}},
        }
    )
    jinmai = PGBuild.from_dict(
        {
            "spirit_tier": "supreme",
            "spirit_power": {
                "power_id": "jinmai",
                "rolls": {"proc_pct": 85, "per_disrupt_pct": 10, "seal_stacks": 3},
            },
        }
    )

    assert xuanjia.spirit_power is not None
    assert xuanjia.spirit_power.rolls == {"def_pct": 100, "proc_pct": 55, "heal_down_pct": 50}
    assert jinmai.spirit_power is not None
    assert jinmai.spirit_power.rolls == {"proc_pct": 35, "per_disrupt_pct": 5}


def test_proving_ground_spirit_tier_changes_normalize_immediately(services) -> None:
    proving_ground = ProvingGroundService(services.combat, random.Random(1))
    wanzhou = PGBuild(
        spirit_tier="mid",
        spirit_power=SpiritPowerEntry(
            "wanzhou",
            {"curse_on_hit": 1, "extra_curse_pct": 50, "burst_threshold": 3, "debuff_rolls_per_curse": 3},
        ),
    )
    message, upgraded = proving_ground.upgrade_spirit_tier(wanzhou)
    assert upgraded is True and "上品" in message
    assert wanzhou.spirit_power is not None
    assert wanzhou.spirit_power.rolls == {
        "curse_on_hit": 2,
        "extra_curse_pct": 0,
        "burst_threshold": 4,
        "debuff_rolls_per_curse": 4,
    }

    xuanjia = PGBuild(
        spirit_tier="supreme",
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 100, "proc_pct": 80, "heal_down_pct": 60}),
    )
    run = ProvingGroundRun(character_id=1, pending_affix_ops=0)
    proving_ground._apply_lingshi("accept", xuanjia, run, None)
    assert xuanjia.spirit_tier == "peak"
    assert xuanjia.spirit_power is not None
    assert xuanjia.spirit_power.rolls == {"def_pct": 80, "proc_pct": 40, "heal_down_pct": 50}


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
        spirit_power=SpiritPowerEntry("shisheng", {"heal_pct": 5}),
    )
    defender = services.combat.create_combatant(name="枯木", atk=30, defense=400, agility=10)

    baseline = services.combat.run_battle(attacker_without_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker_with_spirit, defender, rng=roller)

    assert empowered.challenger_hp_after >= baseline.challenger_hp_after
    assert any(log.text and "噬生吞回血气" in log.text for log in empowered.logs)


def test_shisheng_heals_from_followup_damage(services) -> None:
    combat = services.combat
    attacker = _spirit_state(
        services,
        "噬者",
        atk=100,
        defense=50,
        spirit_power=SpiritPowerEntry("shisheng", {"heal_pct": 5}),
    )
    target = _spirit_state(services, "木人", defense=50)
    attacker.hp = 100
    logs: list = []
    actual = combat._apply_typed_damage(
        target,
        200,
        _NORMAL_DAMAGE_PROFILE,
        actor=attacker,
        round_no=1,
        logs=logs,
    )
    assert actual > 0
    assert attacker.hp == 100 + max(1, actual * 5 // 100)
    assert any(log.text and "噬生吞回血气" in log.text for log in logs)


def test_xuekuang_burns_hp_and_stats_scale_with_self_loss(services) -> None:
    combat = services.combat
    state = _spirit_state(
        services,
        "血狂者",
        atk=100,
        defense=100,
        agility=100,
        spirit_power=SpiritPowerEntry("xuekuang", {"burn_pct": 5, "loss_step_pct": 10, "stat_pct": 5}),
    )
    assert state.get_max_hp() == 1000
    logs = combat._trigger_xuekuang_round_start(1, state)
    assert state.hp == 950
    assert state.xuekuang_lost_hp == 50
    assert combat._xuekuang_stat_pct(state) == 0
    assert combat._current_atk(state) == 100
    assert not any(status.name == "血狂" for status in state.statuses)
    assert any(log.text and "血狂燃精" in log.text for log in logs)

    combat._trigger_xuekuang_round_start(2, state)
    assert state.hp == 900
    assert state.xuekuang_lost_hp == 100
    assert combat._xuekuang_stat_pct(state) == 5
    assert combat._current_atk(state) == 105
    assert combat._current_defense(state) == 105
    assert combat._current_agility(state) == 105
    assert state.get_max_hp() == 1050

    state.hp = 1000
    assert combat._xuekuang_stat_pct(state) == 5
    assert combat._current_atk(state) == 105
    assert state.get_max_hp() == 1050

    combat._apply_damage(state, 100, respects_resilience=False)
    assert state.xuekuang_lost_hp == 200
    assert combat._xuekuang_stat_pct(state) == 5
    assert combat._current_atk(state) == 105
    assert state.get_max_hp() == 1050

    combat._apply_damage(state, 5, respects_resilience=False)
    assert combat._xuekuang_stat_pct(state) == 10
    assert combat._current_atk(state) == 110
    assert state.get_max_hp() == 1100


def test_fenmai_applies_burn_and_shreds_max_hp_by_burn_damage(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "烬心",
        atk=100,
        spirit_power=SpiritPowerEntry("fenmai", {"burn_stacks": 2}),
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"burn_stacks": 1, "burn_atk_pct": 10}),),
    )
    target = _spirit_state(services, "荒甲", defense=100)
    logs = combat._trigger_spirit_on_hit(1, actor, target, 10, CombatRoller([]), source=_DamageSource.ATTACK, scene=set())
    assert combat._burn_stacks(target) == 2
    assert any(log.text and "附 2 层灼烧" in log.text for log in logs)

    before_max = target.get_max_hp()
    round_logs = combat._trigger_round_end(1, actor, target, CombatRoller([]))
    burn_logs = [log for log in round_logs if log.text and "层灼烧侵蚀" in log.text]
    fenmai_logs = [log for log in round_logs if log.text and "焚脉" in log.text and "上限" in log.text]
    assert len(burn_logs) == 1
    assert burn_logs[0].damage == 20
    assert target.get_max_hp() < before_max
    assert len(fenmai_logs) == 1


def test_burn_batch_hides_zero_damage_and_merges_followups(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "焚者",
        atk=100,
        spirit_power=SpiritPowerEntry("shisheng", {"heal_pct": 10}),
    )
    actor.hp = 100
    target = _spirit_state(
        services,
        "木人",
        defense=200,
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 50}),
    )
    target.roller = CombatRoller([0.0, 0.99, 0.99])
    combat._apply_burn_to_target(target, actor, stacks=3, per_stack_pct=10, round_no=1, logs=[])
    round_logs = combat._trigger_round_end(1, actor, target, CombatRoller([]))
    texts = [log.text for log in round_logs if log.text]
    burn_logs = [log for log in round_logs if log.text and "层灼烧侵蚀" in log.text]
    block_logs = [text for text in texts if "完全格挡" in text]
    shisheng_logs = [text for text in texts if "噬生吞回血气" in text]
    assert block_logs == ["木人 的玄甲骤然张开，完全格挡本次伤害。"]
    assert len(burn_logs) == 1
    assert burn_logs[0].damage == 20
    assert all(log.text and "造成" in log.text and "余血" in log.text for log in burn_logs)
    assert len(shisheng_logs) == 1


def test_burn_batch_merges_consecutive_xuanjia_blocks(services) -> None:
    combat = services.combat
    actor = _spirit_state(services, "焚者", atk=100)
    target = _spirit_state(
        services,
        "木人",
        defense=200,
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 100}),
    )
    combat._apply_burn_to_target(target, actor, stacks=3, per_stack_pct=10, round_no=1, logs=[])
    round_logs = combat._trigger_round_end(1, actor, target, CombatRoller([]))
    texts = [log.text for log in round_logs if log.text]
    assert texts.count("木人 的玄甲连挡 3 次灼烧。") == 1
    assert not any("完全格挡本次伤害" in text for text in texts)
    assert not any("层灼烧侵蚀" in text for text in texts)


def test_burn_batch_merges_consecutive_fengdun_dodges(services) -> None:
    combat = services.combat
    actor = _spirit_state(services, "焚者", atk=100, agility=1)
    target = _spirit_state(
        services,
        "木人",
        defense=200,
        agility=1000,
        spirit_power=SpiritPowerEntry("fengdun", {"per_wind_pct": 10, "agi_boost_pct": 10}),
    )
    target.roller = CombatRoller([0.0, 0.0, 0.0])
    combat._apply_burn_to_target(target, actor, stacks=3, per_stack_pct=10, round_no=1, logs=[])
    round_logs = combat._trigger_round_end(1, actor, target, CombatRoller([]))
    texts = [log.text for log in round_logs if log.text]
    dodge_logs = [text for text in texts if "风遁连闪" in text or "风遁闪开本次伤害" in text]
    assert dodge_logs == ["木人 以风遁连闪 3 次灼烧，叠至第 3 层，攻势与身法同涨。"]
    assert not any("层灼烧侵蚀" in text for text in texts)


def test_burn_batch_splits_when_huichun_interrupts(services) -> None:
    combat = services.combat
    actor = _spirit_state(services, "焚者", atk=100)
    target = _spirit_state(
        services,
        "青山",
        defense=100,
        affixes=(ArtifactAffixEntry(1, "huichun", {"heal_pct": 50, "shengxi_stacks": 3}),),
    )
    target.hp = 530
    combat._apply_burn_to_target(target, actor, stacks=4, per_stack_pct=20, round_no=1, logs=[])
    round_logs = combat._trigger_round_end(1, actor, target, CombatRoller([]))
    texts = [log.text or "" for log in round_logs]
    burn_logs = [log for log in round_logs if log.text and "层灼烧侵蚀" in log.text]
    assert any("回春发动" in text for text in texts)
    assert any("余势未尽" in text for text in texts)
    assert len(burn_logs) == 2
    assert burn_logs[0].damage == 30
    assert burn_logs[1].damage == 40


def test_shiyan_explodes_once_per_cost_and_keeps_remainder(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "蚀焰主",
        atk=100,
        spirit_power=SpiritPowerEntry("shiyan", {"cost_stacks": 10, "per_burn_pct": 50, "wound_stacks": 1}),
    )
    target = _spirit_state(services, "木人", defense=800)
    combat._apply_burn_to_target(target, actor, stacks=15, per_stack_pct=20, round_no=1, logs=[])
    logs = combat._trigger_shiyan_explodes(1, actor, target, CombatRoller([]))
    explode_logs = [log for log in logs if log.text and "蚀焰倾泻而出" in log.text]
    assert len(explode_logs) == 1
    assert combat._burn_stacks(target) == 5
    assert any(log.text and "创伤" in log.text for log in logs)


def test_shiyan_can_explode_twice_when_stacks_cover_two_costs(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "蚀焰主",
        atk=100,
        spirit_power=SpiritPowerEntry("shiyan", {"cost_stacks": 10, "per_burn_pct": 20, "wound_stacks": 1}),
    )
    target = _spirit_state(services, "铁壁", defense=10_000)
    combat._apply_burn_to_target(target, actor, stacks=20, per_stack_pct=1, round_no=1, logs=[])
    logs = combat._trigger_shiyan_explodes(1, actor, target, CombatRoller([]))
    explode_logs = [log for log in logs if log.text and "蚀焰倾泻而出" in log.text]
    assert len(explode_logs) == 2
    assert combat._burn_stacks(target) == 0


def test_shiyan_explosion_respects_damage_reduction(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "蚀焰主",
        atk=80,
        spirit_power=SpiritPowerEntry("shiyan", {"cost_stacks": 5, "per_burn_pct": 50, "wound_stacks": 1}),
    )
    target = _spirit_state(services, "守势", defense=800)
    combat._apply_burn_to_target(target, actor, stacks=5, per_stack_pct=20, round_no=1, logs=[])
    logs_no = combat._trigger_shiyan_explodes(1, actor, target, CombatRoller([]))
    dmg_no = next(log.damage for log in logs_no if log.text and "蚀焰倾泻而出" in log.text)

    actor_red = _spirit_state(
        services,
        "蚀焰主",
        atk=80,
        spirit_power=SpiritPowerEntry("shiyan", {"cost_stacks": 5, "per_burn_pct": 50, "wound_stacks": 1}),
    )
    target_red = _spirit_state(services, "守势", defense=800)
    combat._add_status(target_red, _StatusEffect("守势", damage_reduction_pct=80))
    combat._apply_burn_to_target(target_red, actor_red, stacks=5, per_stack_pct=20, round_no=1, logs=[])
    logs_red = combat._trigger_shiyan_explodes(1, actor_red, target_red, CombatRoller([]))
    dmg_red = next(log.damage for log in logs_red if log.text and "蚀焰倾泻而出" in log.text)
    assert dmg_red < dmg_no


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



def test_jueming_converts_curse_seals_to_scaled_hp_damage_and_clears_at_three(services) -> None:
    combat = services.combat
    owner_snapshot = combat.create_combatant(
        name="绝命主", atk=100, defense=10, agility=50,
        spirit_power=SpiritPowerEntry("jueming", {"omen_cost": 2, "hp_pct": 10, "heal_down_pct": 40}),
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
    assert target.hp == target.get_max_hp() - 10
    assert any(log.text and "凝成第 1 层死兆" in log.text for log in logs)

    combat._add_curse_seal(target, owner, 2)
    combat._settle_jueming_marks(2, owner, target)
    assert combat._death_omen_count(target) == 2
    assert target.hp == target.get_max_hp() - 30

    combat._add_curse_seal(target, owner, 2)
    logs = combat._settle_jueming_marks(3, owner, target)
    assert combat._death_omen_count(target) == 0
    assert target.hp == target.get_max_hp() - 60
    assert any(log.text and "三重死兆散尽" in log.text for log in logs)


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


@pytest.mark.parametrize(
    ("tier", "def_pct", "proc_range", "heal_down_range"),
    [
        ("low", 10, (1, 10), (10, 20)),
        ("mid", 30, (10, 20), (20, 30)),
        ("high", 50, (20, 30), (30, 40)),
        ("peak", 80, (30, 40), (40, 50)),
        ("supreme", 100, (45, 55), (50, 60)),
    ],
)
def test_xuanjia_tier_values_and_battle_start_hp(services, tier, def_pct, proc_range, heal_down_range) -> None:
    definition = get_spirit_power_definition("xuanjia")
    ranges = {key: (low, high) for key, low, high in definition.roll_ranges_by_tier[tier]}
    assert ranges == {"def_pct": (def_pct, def_pct), "proc_pct": proc_range, "heal_down_pct": heal_down_range}

    state = _spirit_state(
        services,
        "玄甲主",
        defense=100,
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": def_pct, "proc_pct": 0}),
    )
    services.combat._trigger_battle_start(1, state, set())
    assert state.get_max_hp() == 1000 + 1000 * def_pct // 100
    assert state.hp == state.get_max_hp()
    services.combat._trigger_battle_start(1, state, set())
    assert state.get_max_hp() == 1000 + 1000 * def_pct // 100


def test_xuanjia_reduces_healing_received(services) -> None:
    combat = services.combat
    state = _spirit_state(
        services,
        "玄甲主",
        defense=100,
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 0, "heal_down_pct": 50}),
    )
    state.hp = 500
    assert combat._heal_by_damage(state, 200, 100) == 100


def test_xuanjia_blocks_each_damage_packet_before_shield(services) -> None:
    combat = services.combat
    target = _spirit_state(
        services,
        "玄甲主",
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 50}),
    )
    target.roller = CombatRoller([0.0, 0.99, 0.0])
    combat._add_status(target, _StatusEffect("护盾", shield=100))
    logs = []

    assert combat._apply_damage(target, 40, can_be_shielded=True, logs=logs) == 0
    assert combat._total_shield(target) == 100
    assert combat._apply_damage(target, 40, can_be_shielded=True, logs=logs) == 0
    assert combat._total_shield(target) == 60
    assert combat._apply_damage(target, 40, can_be_shielded=True, logs=logs) == 0
    assert combat._total_shield(target) == 60
    assert len([log for log in logs if log.text and "完全格挡" in log.text]) == 2


def test_xuanjia_full_battle_blocks_attack_and_burn_as_separate_packets(services) -> None:
    combat = services.combat
    attacker = combat.create_combatant(
        "灼魂主",
        atk=100,
        defense=100,
        agility=200,
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"burn_stacks": 1, "burn_atk_pct": 25}),),
    )
    defender = combat.create_combatant(
        "玄甲主",
        atk=10,
        defense=100,
        agility=100,
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 100}),
    )
    original_max_rounds = combat.max_rounds
    combat.max_rounds = 1
    try:
        result = combat.run_battle(attacker, defender, rng=CombatRoller([]))
    finally:
        combat.max_rounds = original_max_rounds

    attack_logs = [
        log
        for log in result.logs
        if log.text is None and log.actor_name == "灼魂主" and log.target_name == "玄甲主"
    ]
    block_logs = [log for log in result.logs if log.text and "玄甲" in log.text and "完全格挡" in log.text]
    assert len(attack_logs) == 1 and attack_logs[0].damage == 0
    assert any(log.text and "附 1 层灼烧" in log.text for log in result.logs)
    assert len(block_logs) == 2
    assert result.defender_hp_after == result.defender_max_hp
    assert not any(log.text and "层灼烧侵蚀" in log.text for log in result.logs)


def test_jueming_applies_omen_even_when_xuanjia_blocks_damage(services) -> None:
    combat = services.combat
    owner = _spirit_state(
        services,
        "绝命主",
        spirit_power=SpiritPowerEntry("jueming", {"omen_cost": 2, "hp_pct": 20, "heal_down_pct": 40}),
    )
    target = _spirit_state(
        services,
        "玄甲主",
        spirit_power=SpiritPowerEntry("xuanjia", {"def_pct": 10, "proc_pct": 50}),
    )
    combat._add_curse_seal(target, owner, 2)
    target.roller = CombatRoller([0.0])
    hp_before = target.hp

    logs = combat._settle_jueming_marks(1, owner, target)
    assert combat._curse_seal_count(target) == 0
    assert combat._death_omen_count(target) == 1
    assert target.hp == hp_before
    assert any(log.text and "完全格挡本次伤害" in log.text for log in logs)

    combat._add_curse_seal(target, owner, 2)
    target.roller = CombatRoller([0.99])
    combat._settle_jueming_marks(2, owner, target)
    assert combat._curse_seal_count(target) == 0
    assert combat._death_omen_count(target) == 2
    assert target.hp == hp_before - owner.get_max_hp() * 40 // 100


def test_jinmai_probability_seal_and_break_spirit_lifecycle(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "禁脉主",
        spirit_power=SpiritPowerEntry("jinmai", {"proc_pct": 0, "per_disrupt_pct": 5}),
    )
    target = _spirit_state(services, "靶子", atk=100)

    combat._trigger_spirit_on_hit(1, actor, target, 1, CombatRoller([0.0]), source=_DamageSource.ATTACK, scene=set())
    assert combat._status_count(target, "封禁行动") == 0
    assert combat._status_count(target, "破步") == 0
    assert combat._status_count(target, "创伤") == 0

    combat._add_status(target, _StatusEffect("破步", stacks=2, is_debuff=True, source=actor))
    combat._add_status(target, _StatusEffect("创伤", stacks=2, is_debuff=True, source=actor))
    combat._trigger_spirit_on_hit(1, actor, target, 1, CombatRoller([0.19]), source=_DamageSource.ATTACK, scene=set())
    assert combat._status_count(target, "封禁行动") == 1
    assert combat._status_count(target, "破封灵势") == 1
    assert combat._current_atk(target) == 110
    assert combat._remove_one_positive_status(target) is None
    assert combat._remove_all_status_effects(target) == 5
    assert combat._status_count(target, "破封灵势") == 1
    combat._add_action_seal(target, actor, 1)

    _, can_act = combat._trigger_before_action(2, target, actor)
    assert can_act is False
    assert combat._status_count(target, "破封灵势") == 1
    _, can_act = combat._trigger_before_action(3, target, actor)
    assert can_act is True
    before = combat._current_atk(target)
    action_logs = combat._resolve_action(3, target, actor, CombatRoller([0.99, 0.99]), set())
    assert before == 110
    assert any(log.text is None and log.actor_name == "靶子" and log.damage == 110 for log in action_logs)
    assert combat._status_count(target, "破封灵势") == 0
    assert combat._current_atk(target) == 100


def test_break_spirit_is_consumed_by_counterattack(services) -> None:
    combat = services.combat
    counterer = _spirit_state(
        services,
        "反击者",
        affixes=(ArtifactAffixEntry(1, "huanbu", {"dodge_pct": 5, "counter_pct": 100}),),
    )
    attacker = _spirit_state(services, "来犯者")
    combat._add_status(counterer, _StatusEffect("幻步", stacks=3, dodge_bonus_pct=5))
    combat._add_status(counterer, _StatusEffect("破封灵势", stacks=2, atk_pct=10, cleanseable=False))

    logs = combat._trigger_on_dodge(1, counterer, attacker, set())

    assert any(log.text and "幻步虚影一闪" in log.text for log in logs)
    assert combat._status_count(counterer, "破封灵势") == 0


def test_zhuifeng_first_mover_hunt_and_permanent_chase(services) -> None:
    combat = services.combat
    power = SpiritPowerEntry("zhuifeng", {"r1_crit_bonus": 30, "r1_agility_pct": 15})
    actor = _spirit_state(services, "追风主", agility=100, spirit_power=power)
    target = _spirit_state(services, "靶子", agility=100)

    actor.current_round = 1
    assert combat._zhuifeng_force_hit(actor) is False
    assert combat._zhuifeng_crit_bonus_pct(actor) == 0
    actor.is_first_mover = True
    assert combat._zhuifeng_force_hit(actor) is True
    assert combat._zhuifeng_crit_bonus_pct(actor) == 30
    first_logs = combat._resolve_action(1, actor, target, CombatRoller([0.99]), set())
    second_logs = combat._resolve_action(1, actor, target, CombatRoller([0.0]), set())
    assert any(log.text is None and not log.dodged for log in first_logs)
    assert any(log.text is None and log.dodged for log in second_logs)
    actor.current_round = 3
    assert combat._zhuifeng_crit_bonus_pct(actor) == 30
    actor.current_round = 4
    assert combat._zhuifeng_crit_bonus_pct(actor) == 0

    for _ in range(15):
        combat._trigger_spirit_on_crit(4, actor, target, 1, CombatRoller([]))
    assert combat._status_count(actor, "追猎") == 12
    assert combat._current_agility(actor) == 280
    assert combat._remove_one_positive_status(actor) is None
    assert combat._remove_all_status_effects(actor) == 0
    assert combat._status_count(actor, "追猎") == 12


class _LeifaRoller:
    def __init__(self, victims) -> None:
        self._victims = iter(victims)

    def randint(self, start: int, end: int) -> int:
        return end

    def choice(self, items):
        victim = next(self._victims)
        assert victim in items
        return victim


def test_leifa_consumes_layers_strikes_rods_and_returns_one_mark(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "雷罚主",
        atk=100,
        spirit_power=SpiritPowerEntry("leifa", {"cost_stacks": 3, "strikes_min": 2, "strikes_max": 2, "burst_pct": 50}),
    )
    target = _spirit_state(services, "靶子", defense=100)
    combat._add_target_leihen(target, actor, 5)
    combat._add_target_leihen(actor, actor, 1)
    hp_before_actor = actor.hp
    hp_before_target = target.hp

    logs = combat._trigger_leifa(1, actor, target, _LeifaRoller([target, actor]))

    assert combat._target_leihen_count(target) == 3
    assert combat._target_leihen_count(actor) == 1
    assert target.hp < hp_before_target
    assert actor.hp < hp_before_actor
    assert any(log.text and "小型雷劫" in log.text for log in logs)


def test_wanzhou_extra_curse_uses_strict_probability_boundary(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "万咒主",
        spirit_power=SpiritPowerEntry(
            "wanzhou",
            {"curse_on_hit": 1, "extra_curse_pct": 50, "burst_threshold": 99, "debuff_rolls_per_curse": 2},
        ),
    )
    target = _spirit_state(services, "靶子")

    combat._trigger_spirit_on_hit(
        1,
        actor,
        target,
        1,
        CombatRoller([0.5]),
        source=_DamageSource.ATTACK,
        scene=set(),
    )

    assert combat._curse_seal_count(target) == 1


def test_wanzhou_uses_leihen_pool_without_action_seal(services) -> None:
    combat = services.combat
    actor = _spirit_state(
        services,
        "万咒主",
        spirit_power=SpiritPowerEntry("wanzhou", {"debuff_rolls_per_curse": 20, "seal_weight": 999}),
    )
    target = _spirit_state(services, "靶子")
    combat._add_curse_seal(target, actor, 10)

    logs = combat._trigger_wanzhou_burst(1, actor, target, random.Random(2))
    assert combat._status_count(target, "封禁行动") == 0
    assert combat._target_leihen_count(target) > 0
    assert any(log.text and "雷殛" in log.text for log in logs)


class _StealRoller:
    def __init__(self, *, chain_rolls: list[int] | None = None) -> None:
        self._chain = iter(chain_rolls or [])

    def choice(self, items):
        return items[0]

    def randint(self, start: int, end: int) -> int:
        return next(self._chain, end)


def test_qiedao_steals_one_stack_and_respects_caps(services) -> None:
    combat = services.combat
    thief = _spirit_state(services, "窃者", spirit_power=SpiritPowerEntry("qiedao", {"chain_pct": 0}))
    victim = _spirit_state(services, "失主")
    combat._add_status(victim, _StatusEffect("灵势", stacks=10, atk_pct=8))
    combat._add_status(thief, _StatusEffect("灵势", stacks=9, atk_pct=8))

    logs = combat._trigger_spirit_round_start(1, thief, victim, _StealRoller())

    assert combat._status_count(victim, "灵势") == 9
    assert combat._status_count(thief, "灵势") == 10
    assert any(log.text and "窃取 1 层「灵势」" in log.text for log in logs)

    logs = combat._trigger_spirit_round_start(2, thief, victim, _StealRoller())
    assert combat._status_count(victim, "灵势") == 9
    assert combat._status_count(thief, "灵势") == 10
    assert logs == []


def test_qiedao_keeps_copied_fields_and_transfers_one_debuff_stack(services) -> None:
    combat = services.combat
    thief = _spirit_state(services, "窃者", spirit_power=SpiritPowerEntry("qiedao", {"chain_pct": 0}))
    victim = _spirit_state(services, "失主")
    combat._add_status(
        victim,
        _StatusEffect("狂锋", stacks=2, damage_dealt_pct=70, remaining_hits=1, active_from_round=3),
    )

    logs = combat._trigger_spirit_round_start(1, thief, victim, _StealRoller())
    stolen = next(status for status in thief.statuses if status.name == "狂锋")
    leftover = next(status for status in victim.statuses if status.name == "狂锋")
    assert leftover.stacks == 1
    assert stolen.stacks == 1
    assert stolen.active_from_round == 3
    assert stolen.remaining_hits == 1
    assert stolen.damage_dealt_pct == 70
    assert stolen.source is thief
    assert any(log.text and "窃取 1 层「狂锋」" in log.text for log in logs)

    thief.statuses = [status for status in thief.statuses if status.name != "狂锋"]
    victim.statuses = [status for status in victim.statuses if status.name != "狂锋"]
    combat._add_status(
        thief,
        _StatusEffect("创伤", stacks=5, damage_taken_pct=5, heal_received_pct=-8, is_debuff=True, source=victim),
    )
    logs = combat._trigger_spirit_round_start(2, thief, victim, _StealRoller())
    transferred = next(status for status in victim.statuses if status.name == "创伤")
    remaining = next(status for status in thief.statuses if status.name == "创伤")
    assert remaining.stacks == 4
    assert transferred.stacks == 1
    assert transferred.heal_received_pct == -8
    assert transferred.source is thief
    assert any(log.text and "1 层「创伤」" in log.text for log in logs)


def test_qiedao_cannot_steal_shield_or_uncleanseable_status(services) -> None:
    combat = services.combat
    thief = _spirit_state(services, "窃者", spirit_power=SpiritPowerEntry("qiedao", {"chain_pct": 0}))
    victim = _spirit_state(services, "失主")
    combat._add_status(victim, _StatusEffect("固本", shield=200, cleanseable=False))
    combat._add_status(victim, _StatusEffect("追猎", agility_pct=10, cleanseable=False))
    combat._add_status(thief, _StatusEffect("死兆", heal_received_pct=-40, is_debuff=True, cleanseable=False, source=victim))

    logs = combat._trigger_spirit_round_start(1, thief, victim, _StealRoller())

    assert combat._status_count(victim, "固本") == 1
    assert combat._status_count(victim, "追猎") == 1
    assert combat._status_count(thief, "死兆") == 1
    assert combat._status_count(victim, "死兆") == 0
    assert logs == []


def test_qiedao_cannot_steal_pending_followup_strike(services) -> None:
    combat = services.combat
    thief = _spirit_state(services, "窃者", spirit_power=SpiritPowerEntry("qiedao", {"chain_pct": 0}))
    victim = _spirit_state(services, "失主")
    combat._add_status(victim, _StatusEffect("涤世·净化", bonus_damage=300000, remaining_hits=1))
    combat._add_status(victim, _StatusEffect("春生·追击", bonus_damage=400, remaining_hits=1))
    combat._add_status(victim, _StatusEffect("风刃", guarantee_crit=True, damage_dealt_pct=50, remaining_hits=1))
    combat._add_status(victim, _StatusEffect("碎阙", damage_dealt_pct=40, remaining_hits=1))
    combat._add_status(victim, _StatusEffect("压阵", damage_dealt_pct=50, remaining_hits=1))
    combat._add_status(victim, _StatusEffect("灵势", atk_pct=8))

    logs = combat._trigger_spirit_round_start(1, thief, victim, _StealRoller())

    assert combat._status_count(victim, "涤世·净化") == 1
    assert combat._status_count(victim, "春生·追击") == 1
    assert combat._status_count(victim, "风刃") == 1
    assert combat._status_count(victim, "碎阙") == 1
    assert combat._status_count(thief, "涤世·净化") == 0
    assert combat._status_count(thief, "春生·追击") == 0
    assert combat._status_count(thief, "风刃") == 0
    assert combat._status_count(thief, "碎阙") == 0
    assert combat._status_count(victim, "压阵") == 0
    assert combat._status_count(thief, "压阵") == 1
    assert combat._status_count(victim, "灵势") == 1
    assert combat._status_count(thief, "灵势") == 0
    assert any(log.text and "窃取 1 层「压阵」" in log.text for log in logs)


def test_fanji_reflects_attack_pipeline_total(services) -> None:
    """反棘：主手 + 破空追击（普攻类）合计后按比例一次反弹。"""
    combat = services.combat
    fanji = SpiritPowerEntry("fanji", {"reflect_pct": 50})
    actor = _spirit_state(
        services,
        "来犯者",
        atk=1000,
        defense=10000,
        agility=100,
        affixes=(ArtifactAffixEntry(1, "pokong", {"damage_ratio_pct": 100, "guard_bonus_pct": 0}),),
    )
    target = _spirit_state(services, "棘主", atk=100, defense=100000, agility=100, spirit_power=fanji)

    logs = combat._resolve_action(1, actor, target, CombatRoller([0.99, 0.0]), set())

    # 主手：暴击 1000 × (1.5 + 0.5×1000/101000) = 1504；破空追击：1000×100% = 1000
    assert actor.action_attack_damage_dealt == 1504 + 1000
    assert target.hp == target.get_max_hp() - 2504
    # 反弹一次，50%：2504 // 2 = 1252
    assert actor.hp == actor.get_max_hp() - 1252
    assert sum(1 for log in logs if log.text and "反棘回卷而出" in log.text) == 1


def test_fanji_ignores_elemental_burst_damage(services) -> None:
    """反棘：蚀焰引爆（元素·火）不进入反弹基数。"""
    combat = services.combat
    shiyan = SpiritPowerEntry("shiyan", {"cost_stacks": 2, "per_burn_pct": 50, "wound_stacks": 0})
    fanji = SpiritPowerEntry("fanji", {"reflect_pct": 50})
    actor = _spirit_state(services, "焚者", atk=1000, defense=10000, agility=100, spirit_power=shiyan)
    target = _spirit_state(services, "棘主", atk=100, defense=100000, agility=100, spirit_power=fanji)
    combat._apply_burn_to_target(target, actor, stacks=2, per_stack_pct=20, round_no=1, logs=[])

    logs = combat._resolve_action(1, actor, target, CombatRoller([0.99, 0.0]), set())

    # 主手暴击 1504；蚀焰引爆 2 层 × 50% 杀伐 = 1000，属元素伤害不计入反弹基数
    assert actor.action_attack_damage_dealt == 1504
    assert target.hp == target.get_max_hp() - 1504 - 1000
    assert actor.hp == actor.get_max_hp() - (1504 * 50 // 100)
    assert sum(1 for log in logs if log.text and "反棘回卷而出" in log.text) == 1


def test_fanji_reflect_does_not_retrigger_and_fires_posthumously(services) -> None:
    """双方都持反棘时反弹不再触发反弹；棘主被击杀后仍完成死亡反弹。"""
    combat = services.combat
    actor_fanji = SpiritPowerEntry("fanji", {"reflect_pct": 50})
    target_fanji = SpiritPowerEntry("fanji", {"reflect_pct": 50})
    actor = _spirit_state(services, "来犯者", atk=10000, defense=100, agility=100, spirit_power=actor_fanji)
    target = _spirit_state(services, "棘主", atk=100, defense=100, agility=100, spirit_power=target_fanji)

    combat._resolve_action(1, actor, target, CombatRoller([0.99, 0.0]), set())

    # 主手暴击远超棘主生命，棘主阵亡：实际伤害以剩余生命为限
    assert target.hp == 0
    main_actual = 1000
    # 死者仍反弹，且反弹 packet 不进入任何基数、不再被再次反弹
    assert actor.hp == actor.get_max_hp() - (main_actual * 50 // 100)
    assert target.hp == 0


def test_fanji_no_reflect_on_dodge(services) -> None:
    """攻击被闪避时无普攻类伤害，不反弹。"""
    combat = services.combat
    fanji = SpiritPowerEntry("fanji", {"reflect_pct": 90})
    actor = _spirit_state(services, "来犯者", atk=1000, defense=10000, agility=100)
    target = _spirit_state(services, "棘主", atk=100, defense=100000, agility=100, spirit_power=fanji)

    combat._resolve_action(1, actor, target, CombatRoller([0.0]), set())

    assert actor.action_attack_damage_dealt == 0
    assert actor.hp == actor.get_max_hp()
    assert target.hp == target.get_max_hp()
