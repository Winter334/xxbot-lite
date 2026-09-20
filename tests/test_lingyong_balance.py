from __future__ import annotations

from dataclasses import asdict
import random

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SpiritPowerEntry
from bot.services.combat_service import (
    CombatantSnapshot,
    CombatService,
    _BURN_DOT_PROFILE,
    _CombatState,
    _DamageSource,
    _NORMAL_DAMAGE_PROFILE,
    _StatusEffect,
)


class FixedRoller(random.Random):
    def __init__(self, *values: float) -> None:
        super().__init__(42)
        self.values = iter(values)

    def random(self) -> float:
        return next(self.values, 0.99)


def state(
    name: str,
    *,
    power: str | None = None,
    rolls: dict | None = None,
    atk: int = 100,
    hp: int = 1000,
    affixes: tuple[ArtifactAffixEntry, ...] = (),
    **snapshot_fields,
) -> _CombatState:
    snapshot = CombatantSnapshot(
        name, atk, 100, 100, hp,
        affixes=affixes,
        spirit_power=SpiritPowerEntry(power, rolls or {}) if power else None,
        **snapshot_fields,
    )
    return _CombatState(snapshot, hp, effective_max_hp=hp, roller=FixedRoller())


def lingshi(combat: CombatService, owner: _CombatState, layers: int, *, atk_pct: int = 9) -> None:
    combat._add_status(owner, _StatusEffect("灵势", stacks=layers, atk_pct=atk_pct))


def true_strikes(logs):
    return [log for log in logs if log.text and "灵涌贯体" in log.text]


@pytest.mark.parametrize(("power", "cap"), [(None, 10), ("lingyu", 10), ("lingyong", 20)])
def test_lingshi_capacity_is_specific_to_lingyong(power, cap) -> None:
    combat = CombatService()
    owner = state("owner", power=power)
    lingshi(combat, owner, 30)

    assert combat._status_count(owner, "灵势") == cap
    assert combat._current_atk(owner) == 190
    assert combat._status_stack_cap("灵势", owner) == cap


def test_opening_supply_unchanged_and_no_passive_generation() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong", rolls={"start_stacks": 5, "per_stack_pct": 12})
    enemy = state("enemy")
    combat._trigger_battle_start(1, owner, set())
    for turn in range(1, 8):
        combat._trigger_round_start(turn, owner, enemy, FixedRoller(), set())

    assert combat._status_count(owner, "灵势") == 5
    assert combat._current_atk(owner) == 100
    assert combat._spirit_damage_bonus_pct(owner, enemy) == 60


@pytest.mark.parametrize(("copies", "first_true_round", "full_round"), [(1, 6, 15), (2, 3, 8), (3, 2, 5)])
def test_juling_growth_reaches_twenty_without_extra_stat_inflation(copies, first_true_round, full_round) -> None:
    combat = CombatService()
    owner = state(
        "owner",
        power="lingyong",
        rolls={"start_stacks": 5, "per_stack_pct": 12},
        affixes=tuple(ArtifactAffixEntry(i + 1, "juling", {"atk_pct": 9, "late_damage_pct": 18}) for i in range(copies)),
    )
    enemy = state("enemy", hp=100000)
    combat._trigger_battle_start(1, owner, set())
    first_true = None
    full = None
    for turn in range(1, 22):
        combat._trigger_round_start(turn, owner, enemy, FixedRoller(), set())
        layers = combat._status_count(owner, "灵势")
        if layers > 10 and first_true is None:
            first_true = turn
        if layers == 20 and full is None:
            full = turn

    assert (first_true, full) == (first_true_round, full_round)
    assert combat._status_count(owner, "灵势") == 20
    assert combat._current_atk(owner) == 190
    assert combat._damage_dealt_pct(owner) == 90
    assert combat._spirit_damage_bonus_pct(owner, enemy) == 120


def test_ningshen_crosses_threshold_on_hit_but_respects_twenty_cap() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong", affixes=(ArtifactAffixEntry(1, "ningshen", {"atk_pct": 9}),))
    enemy = state("enemy", hp=10000)
    lingshi(combat, owner, 10)
    logs = combat._resolve_action(1, owner, enemy, FixedRoller(), set())
    assert combat._status_count(owner, "灵势") == 11
    assert len(true_strikes(logs)) == 1
    assert true_strikes(logs)[0].damage == 200
    for turn in range(2, 20):
        combat._trigger_on_hit(turn, owner, enemy, 0, FixedRoller(), set())
    assert combat._status_count(owner, "灵势") == 20
    assert combat._current_atk(owner) == 190


@pytest.mark.parametrize(("layers", "removed"), [(0, 0), (1, 0), (2, 1), (3, 1), (5, 2), (6, 3), (19, 9), (20, 10)])
def test_cleanse_is_two_to_one_prioritizes_burns_and_runs_once(layers, removed) -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    enemy = state("enemy")
    if layers:
        lingshi(combat, owner, layers)
    combat._add_status(owner, _StatusEffect("蔓咒", stacks=7, atk_pct=-12, is_debuff=True, source=enemy))
    combat._add_status(owner, _StatusEffect("灼烧", stacks=9, burn_pct=5, is_debuff=True, source=enemy))
    combat._add_status(owner, _StatusEffect("死兆", is_debuff=True, cleanseable=False))

    combat._trigger_spirit_round_start(1, owner, enemy, FixedRoller())
    combat._trigger_spirit_round_start(1, owner, enemy, FixedRoller())
    assert combat._burn_stacks(owner) == max(0, 9 - removed)
    assert combat._status_count(owner, "蔓咒") == 7 - max(0, removed - 9)
    assert combat._status_count(owner, "死兆") == 1
    assert combat._status_count(owner, "灵势") == layers


def test_cleanse_uses_current_layers_after_juling_and_stacks_with_jinghua() -> None:
    combat = CombatService()
    owner = state(
        "owner", power="lingyong",
        affixes=(
            ArtifactAffixEntry(1, "jinghua", {"stacks": 1}),
            ArtifactAffixEntry(2, "juling", {"atk_pct": 9, "late_damage_pct": 10}),
        ),
    )
    enemy = state("enemy")
    lingshi(combat, owner, 5)
    combat._add_status(owner, _StatusEffect("灼烧", stacks=8, burn_pct=5, is_debuff=True, source=enemy))
    combat._trigger_round_start(1, owner, enemy, FixedRoller(), set())

    assert combat._status_count(owner, "灵势") == 6
    assert combat._burn_stacks(owner) == 4


def test_three_juling_and_max_jinghua_cleanse_curve() -> None:
    combat = CombatService()
    owner = state(
        "owner", power="lingyong", rolls={"start_stacks": 5, "per_stack_pct": 14},
        affixes=(
            *(ArtifactAffixEntry(slot, "juling", {"atk_pct": 9, "late_damage_pct": 18}) for slot in range(1, 4)),
            ArtifactAffixEntry(4, "jinghua", {"stacks": 4}),
        ),
    )
    enemy = state("enemy")
    combat._trigger_battle_start(1, owner, set())
    for turn, (layers, removed) in enumerate(((8, 8), (11, 9), (14, 11), (17, 12), (20, 14)), start=1):
        combat._add_status(owner, _StatusEffect("灼烧", stacks=20, burn_pct=5, is_debuff=True, source=enemy))
        before = combat._burn_stacks(owner)
        combat._trigger_round_start(turn, owner, enemy, FixedRoller(), set())
        assert combat._status_count(owner, "灵势") == layers
        assert before - combat._burn_stacks(owner) == removed


def test_cleanse_preserves_turning_point_and_embers_followups() -> None:
    combat = CombatService()
    owner = state(
        "owner", power="lingyong",
        affixes=(ArtifactAffixEntry(1, "zhuanji", {"damage_pct": 10, "max_layers": 4}),),
    )
    enemy = state(
        "enemy", hp=10000,
        affixes=(ArtifactAffixEntry(1, "yujin", {"proc_pct": 100, "relight_stacks": 2, "relight_burn_pct": 33}),),
    )
    lingshi(combat, owner, 6)
    combat._add_status(owner, _StatusEffect("灼烧", stacks=1, burn_pct=5, is_debuff=True, source=enemy))
    logs = combat._trigger_spirit_round_start(1, owner, enemy, FixedRoller())

    assert combat._burn_stacks(owner) == 2
    assert len([log for log in logs if log.text and "转机发动" in log.text]) == 1
    assert len([log for log in logs if log.text and "余烬未熄" in log.text]) == 1
    assert enemy.hp == 10000 - int(combat._current_atk(owner) * 0.1)


@pytest.mark.parametrize(("layers", "expected"), [(10, 0), (11, 20), (15, 100), (20, 200)])
def test_true_damage_scales_with_current_layers_and_ignores_modifiers(layers, expected) -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong", damage_dealt_basis_points=50000)
    enemy = state(
        "enemy", power="xuanjia", rolls={"proc_pct": 100},
        base_resilience=95, damage_taken_basis_points=90000, damage_reduction_basis_points=9000,
        affixes=(ArtifactAffixEntry(1, "chenchen", {"threshold_pct": 1, "reduction_pct": 100}),),
    )
    lingshi(combat, owner, layers)
    combat._add_status(owner, _StatusEffect("杀伐削弱", atk_pct=-1000, damage_dealt_pct=900))
    combat._add_status(enemy, _StatusEffect("守势", damage_reduction_pct=100))
    combat._add_status(enemy, _StatusEffect("易伤", damage_taken_pct=900, is_debuff=True))
    logs = combat._trigger_lingyong_strike(1, owner, enemy, set())

    assert 1000 - enemy.hp == expected
    assert sum(log.damage for log in logs) == expected


def test_true_damage_uses_effective_max_hp_and_entry_attack_cap() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong", atk=100)
    enemy = state("enemy", hp=1000)
    lingshi(combat, owner, 20)
    combat._add_status(owner, _StatusEffect("蔓咒", atk_pct=-200, is_debuff=True))
    combat._modify_max_hp(enemy, 9000)
    logs = combat._trigger_lingyong_strike(1, owner, enemy, set())

    assert enemy.hp == 9700
    assert sum(log.damage for log in logs) == 300


def test_true_damage_respects_shields_and_low_hp_healing() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    lingshi(combat, owner, 20)
    shielded = state("shielded", power="xuanjia", rolls={"proc_pct": 100}, base_resilience=95)
    combat._add_status(shielded, _StatusEffect("固本", shield=120, cleanseable=False))
    combat._trigger_lingyong_strike(1, owner, shielded, set())
    assert shielded.hp == 920
    assert combat._total_shield(shielded) == 0

    healer = state("healer", affixes=(ArtifactAffixEntry(1, "huichun", {"heal_pct": 50, "shengxi_stacks": 0}),))
    healer.hp = 510
    combat._trigger_lingyong_strike(1, owner, healer, set())
    assert healer.hp == 810
    assert healer.huichun_triggered_thresholds == {50}


def test_blocked_primary_still_triggers_true_damage_but_dodge_does_not() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    enemy = state("enemy", power="xuanjia", rolls={"proc_pct": 100})
    lingshi(combat, owner, 11)

    dodged = combat._resolve_action(1, owner, enemy, FixedRoller(0.0), set())
    assert not true_strikes(dodged)
    assert enemy.hp == 1000
    blocked = combat._resolve_action(2, owner, enemy, FixedRoller(), set())
    assert next(log for log in blocked if log.text is None).damage == 0
    assert len(true_strikes(blocked)) == 1
    assert enemy.hp == 980


def test_followups_do_not_duplicate_true_damage_or_add_it_to_reflection() -> None:
    combat = CombatService()
    owner = state(
        "owner", power="lingyong", hp=10000,
        affixes=(
            ArtifactAffixEntry(1, "pokong", {"damage_ratio_pct": 100, "guard_bonus_pct": 0}),
            ArtifactAffixEntry(2, "pokong", {"damage_ratio_pct": 100, "guard_bonus_pct": 0}),
        ),
    )
    enemy = state("enemy", power="fanji", rolls={"reflect_pct": 50}, hp=100000)
    lingshi(combat, owner, 20)
    logs = combat._resolve_action(1, owner, enemy, FixedRoller(0.99, 0.0), set())

    assert len(true_strikes(logs)) == 1
    reflected = next(log for log in logs if log.text and "反棘回卷" in log.text)
    assert reflected.damage == owner.action_attack_damage_dealt * 50 // 100
    assert 100000 - enemy.hp == owner.action_attack_damage_dealt + 300


@pytest.mark.parametrize("profile", [_BURN_DOT_PROFILE, _NORMAL_DAMAGE_PROFILE])
def test_non_primary_damage_does_not_trigger_true_damage(profile) -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    enemy = state("enemy", hp=10000)
    lingshi(combat, owner, 20)
    logs = []
    combat._apply_typed_damage(enemy, 10, profile, actor=owner, logs=logs)
    logs.extend(combat._trigger_spirit_on_hit(1, owner, enemy, 10, FixedRoller(), source=_DamageSource.COUNTER, scene=set()))
    assert not true_strikes(logs)


def test_dispel_reduces_both_rewards_and_tide_wash_removes_growth() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    enemy = state("enemy", power="dishi", rolls={"threshold": 1, "stack_pct": 0})
    lingshi(combat, owner, 12)
    combat._remove_one_positive_status(owner)
    combat._add_status(owner, _StatusEffect("灼烧", stacks=7, burn_pct=1, source=enemy, is_debuff=True))
    combat._trigger_spirit_round_start(1, owner, enemy, FixedRoller())
    assert combat._burn_stacks(owner) == 2
    assert true_strikes(combat._trigger_lingyong_strike(1, owner, enemy, set()))[0].damage == 20
    combat._remove_one_positive_status(owner)
    assert not combat._trigger_lingyong_strike(2, owner, enemy, set())
    combat._trigger_spirit_round_end(2, enemy, owner, FixedRoller())
    assert combat._status_count(owner, "灵势") == 0


def test_stealing_lingyong_stacks_does_not_raise_thiefs_cap() -> None:
    combat = CombatService()
    owner = state("owner", power="lingyong")
    thief = state("thief", power="qiedao", rolls={"chain_pct": 0})
    lingshi(combat, owner, 20)
    lingshi(combat, thief, 9)
    combat._trigger_spirit_round_start(1, thief, owner, random.Random(42))
    assert combat._status_count(owner, "灵势") == 19
    assert combat._status_count(thief, "灵势") == 10
    combat._trigger_spirit_round_start(2, thief, owner, random.Random(42))
    assert combat._status_count(owner, "灵势") == 19


def test_full_battle_remains_reproducible_without_mutating_inputs() -> None:
    combat = CombatService()
    owner = state(
        "owner", power="lingyong", rolls={"start_stacks": 5, "per_stack_pct": 12},
        atk=10, hp=10000,
        affixes=(ArtifactAffixEntry(1, "juling", {"atk_pct": 9, "late_damage_pct": 10}),),
    )
    enemy = state(
        "enemy", power="xuanjia", rolls={"def_pct": 100, "proc_pct": 60, "heal_down_pct": 25},
        atk=10, hp=10000,
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"burn_stacks": 4, "burn_atk_pct": 5}),),
    )
    before = [asdict(owner.snapshot), asdict(enemy.snapshot)]
    first = combat.run_battle(owner.snapshot, enemy.snapshot, rng=random.Random(42))
    second = combat.run_battle(owner.snapshot, enemy.snapshot, rng=random.Random(42))
    assert asdict(first) == asdict(second)
    assert [asdict(owner.snapshot), asdict(enemy.snapshot)] == before
    assert true_strikes(first.logs)
