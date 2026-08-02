"""境界基础韧性（base_resilience）回归测试。

设计意图：
- 韧性 baseline 是底层减伤，所有走 _apply_damage 的伤害都按 base_resilience % 扣减
- 唯一豁免：respects_resilience=False（如雷劫引爆机制性必杀真伤），或绝命斩杀直接 target.hp=0
- 韧性梯度按境界：lianqi=0 / zhuji=4 / jiedan=8 / yuanying=12 / huashen=16 / lianxu=20 / heti=24 / dacheng=28 / dujie=32 / weixian=36
- 验证：跨大境界碾压仍保留（高境界攻击力 × 韧性扣减后依然秒杀低境界）；同境界对决伤害被有效压制
"""
from __future__ import annotations

import pytest

from bot.data.realms import REALM_BY_KEYS, REALM_STAGES
from bot.services.combat_service import (
    CombatantSnapshot,
    CombatService,
    _CombatState,
)


# ─── 数据层：韧性梯度断言 ────────────────────────────────────


def test_realm_resilience_ladder_matches_specification() -> None:
    """验证 realms.py 中 base_resilience 阶梯按设计落位。"""
    expected = {
        "lianqi": 0,
        "zhuji": 4,
        "jiedan": 8,
        "yuanying": 12,
        "huashen": 16,
        "lianxu": 20,
        "heti": 24,
        "dacheng": 28,
        "dujie": 32,
        "weixian": 36,
    }
    for stage in REALM_STAGES:
        assert stage.base_resilience == expected[stage.realm_key], (
            f"{stage.display_name} 韧性={stage.base_resilience}, 期望 {expected[stage.realm_key]}"
        )


# ─── 工厂方法：snapshot 透传韧性 ───────────────────────────


def test_create_combatant_passes_base_resilience() -> None:
    """build_combatant_snapshot 入参的 base_resilience 必须落到 snapshot 上。"""
    combat = CombatService()
    snap = combat.create_combatant(
        name="测试",
        atk=100,
        defense=10,
        agility=10,
        base_resilience=20,
    )
    assert snap.base_resilience == 20


def test_default_base_resilience_is_zero() -> None:
    """不显式传 base_resilience 时默认为 0（兼容旧调用方）。"""
    combat = CombatService()
    snap = combat.create_combatant(name="测试", atk=100, defense=10, agility=10)
    assert snap.base_resilience == 0


# ─── 核心扣减：_apply_damage 韧性折扣 ───────────────────────


def _make_state(resilience: int, *, hp: int = 1000, max_hp: int = 1000) -> _CombatState:
    snap = CombatantSnapshot(
        name="target",
        atk=10,
        defense=10,
        agility=10,
        max_hp=max_hp,
        base_resilience=resilience,
    )
    return _CombatState(snapshot=snap, hp=hp)


def test_apply_damage_respects_resilience_default() -> None:
    """默认 respects_resilience=True，目标韧性 20% 应使 1000 伤害降至 800。"""
    combat = CombatService()
    state = _make_state(resilience=20, hp=2000, max_hp=2000)
    actual = combat._apply_damage(state, 1000)
    assert actual == 800
    assert state.hp == 1200


def test_apply_damage_bypass_resilience() -> None:
    """respects_resilience=False（雷劫引爆等）豁免韧性，1000 伤害足额命中。"""
    combat = CombatService()
    state = _make_state(resilience=32, hp=2000, max_hp=2000)
    actual = combat._apply_damage(state, 1000, respects_resilience=False)
    assert actual == 1000
    assert state.hp == 1000


def test_apply_damage_zero_resilience_no_change() -> None:
    """韧性 0（炼气）伤害不扣减。"""
    combat = CombatService()
    state = _make_state(resilience=0, hp=2000, max_hp=2000)
    actual = combat._apply_damage(state, 1000)
    assert actual == 1000


def test_apply_damage_resilience_clamped_to_95() -> None:
    """韧性高于 95 也只按 95% 扣减（至少留 1 伤害）。"""
    combat = CombatService()
    # 直接构造 snapshot 设置一个超额韧性测试 clamp
    state = _make_state(resilience=99, hp=2000, max_hp=2000)
    actual = combat._apply_damage(state, 1000)
    # 95% 扣减 → 50；但 max(1, ...) 保底；这里足够大 → 应得 50
    assert actual == 50


def test_apply_damage_min_one_when_resilience_high() -> None:
    """高韧性 + 极小伤害也至少扣 1 点（避免 0 伤害锁死）。"""
    combat = CombatService()
    state = _make_state(resilience=90, hp=2000, max_hp=2000)
    actual = combat._apply_damage(state, 5)
    # 5 * 10 // 100 = 0 → max(1, 0) = 1
    assert actual == 1


# ─── 跨境界碾压：高境界攻击力压过低境界韧性 ─────────────────


def test_cross_realm_crush_dujie_vs_lianqi() -> None:
    """渡劫·圆满（韧性 32%）打炼气·前期（韧性 0%）依然秒杀。

    实战意义：韧性 baseline 不破坏 carry/crush 玩法。攻击力差距 50000+ × 0.68（=对方韧性扣减无作用）
    远超对方 HP。
    """
    combat = CombatService()
    dujie_stage = REALM_BY_KEYS[("dujie", "perfect")]
    lianqi_stage = REALM_BY_KEYS[("lianqi", "early")]

    attacker = combat.create_combatant(
        name="渡劫前辈",
        atk=dujie_stage.base_atk,
        defense=dujie_stage.base_def,
        agility=dujie_stage.base_agi,
        realm_index=dujie_stage.realm_index,
        base_resilience=dujie_stage.base_resilience,
    )
    defender = combat.create_combatant(
        name="炼气小辈",
        atk=lianqi_stage.base_atk,
        defense=lianqi_stage.base_def,
        agility=lianqi_stage.base_agi,
        realm_index=lianqi_stage.realm_index,
        base_resilience=lianqi_stage.base_resilience,
    )

    result = combat.run_battle(attacker, defender)

    assert result.winner_name == "渡劫前辈"
    assert result.loser_name == "炼气小辈"
    # 跨九境碾压，应在 2 回合内结束
    assert result.rounds <= 2
    # 防守方应被击杀
    assert result.defender_hp_after == 0


# ─── 同境界对决：韧性确实压制了伤害（与无韧性对照） ─────────────


def test_same_resilience_target_takes_less_damage() -> None:
    """对照测试：相同伤害输入下，有韧性目标比无韧性目标承伤更低。

    直接对比 _apply_damage 在不同韧性下的输出，等同于"同境界对决中，韧性 baseline 确实压制了承伤"。
    """
    combat = CombatService()

    # 模拟一次典型攻击伤害
    raw_damage = 5000

    # 无韧目标
    state_zero = _make_state(resilience=0, hp=100000, max_hp=100000)
    # 炼虚韧性 20%
    state_lianxu = _make_state(resilience=20, hp=100000, max_hp=100000)
    # 渡劫韧性 32%
    state_dujie = _make_state(resilience=32, hp=100000, max_hp=100000)

    dmg_zero = combat._apply_damage(state_zero, raw_damage)
    dmg_lianxu = combat._apply_damage(state_lianxu, raw_damage)
    dmg_dujie = combat._apply_damage(state_dujie, raw_damage)

    assert dmg_zero == 5000
    assert dmg_lianxu == 4000  # 5000 * 0.8
    assert dmg_dujie == 3400  # 5000 * 0.68
    # 严格梯度：韧性越高，承伤越低
    assert dmg_dujie < dmg_lianxu < dmg_zero


# ─── 机制性必杀真伤豁免韧性 ─────────────────────────────


def test_mechanical_execute_damage_bypasses_resilience() -> None:
    """直接验证底层为机制性必杀保留的 respects_resilience=False 通道。"""
    combat = CombatService()

    # 高韧性目标
    state_with_kw_false = _make_state(resilience=36, hp=10000, max_hp=10000)
    state_with_kw_true = _make_state(resilience=36, hp=10000, max_hp=10000)

    # 模拟机制性必杀伤害
    judgment_damage = 1000

    actual_bypass = combat._apply_damage(state_with_kw_false, judgment_damage, respects_resilience=False)
    actual_respect = combat._apply_damage(state_with_kw_true, judgment_damage)

    assert actual_bypass == 1000  # 真伤足额
    assert actual_respect == 640  # 1000 * 0.64
    assert actual_bypass > actual_respect


# ─── DOT/普攻管线吃韧性 ────────────────────────────────────


def test_dot_burn_damage_respects_resilience() -> None:
    """灼烧 DOT 走 _BURN_DOT_PROFILE，其内部最终调用 _apply_damage(respects_resilience=True)，
    应吃目标韧性。

    通过对比两个目标承受相同 DOT 伤害但不同韧性的结果验证。
    """
    combat = CombatService()
    from bot.services.combat_service import _BURN_DOT_PROFILE

    assert _BURN_DOT_PROFILE.respects_resilience is True

    # 应用相同 DOT
    state_hi = _make_state(resilience=24, hp=10000, max_hp=10000)
    state_lo = _make_state(resilience=0, hp=10000, max_hp=10000)

    # _apply_typed_damage 末尾调用 _apply_damage 时透传 profile.respects_resilience；
    # 简化为直接调用 _apply_damage(respects=True/False)
    actual_hi = combat._apply_damage(state_hi, 500, respects_resilience=True)
    actual_lo = combat._apply_damage(state_lo, 500, respects_resilience=True)

    assert actual_lo == 500
    assert actual_hi == 500 * 76 // 100  # = 380
    assert actual_hi < actual_lo


# ─── 雷劫真伤 profile 标记 ────────────────────────────────


def test_burn_profile_marked_respects_resilience() -> None:
    """显式断言关键 profile 的 respects_resilience 标记，防止后续被误改。"""
    from bot.services.combat_service import (
        _BURN_DOT_PROFILE,
        _CHUNSHENG_BONUS_PROFILE,
        _SHIYAN_PROFILE,
    )
    assert _BURN_DOT_PROFILE.respects_resilience is True
    assert _SHIYAN_PROFILE.respects_resilience is True
    assert _CHUNSHENG_BONUS_PROFILE.respects_resilience is True
