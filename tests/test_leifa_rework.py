"""雷罚（leifa）重做回归测试。

重做要点（参见 specs / memory：2026-05-12 战斗平衡调整）：
1. 雷罚常驻 +crit_damage_base_pct% 暴击伤害（被动）
2. 暴击时追加 thunder_pct% 雷罚伤害（吃韧性）
3. 暴击给目标烙下「雷殛」标记，上限 3 层
4. 雷殛存在时，目标每次受击额外承受 mark_damage_pct% 普攻基础伤害的雷殛真伤（吃韧性）
5. 雷殛叠满 3 层立即引爆，造成最大生命 judgment_pct% 雷劫真伤（豁免韧性）并清除全部雷殛
6. 配套词条：tianwei 同时挂 crit_pct + crit_damage_pct；leiyin 改 on_crit + 下击增伤 + 3 暴击触发小雷劫真伤；
   liekong 改读 target 雷殛层数提供穿透

战斗 rng 调用顺序提示：
  _resolve_action 依次 roll 闪避检定与暴击检定。
  传 [0.99, 0.0, ...] 模式：0.99 fail dodge → 0.0 < crit_rate → 强制暴击。
"""
from __future__ import annotations

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SpiritPowerEntry, get_spirit_power_definition
from bot.services.combat_service import (
    CombatService,
    _CombatState,
    _StatusEffect,
)


class CombatRoller:
    def __init__(self, random_values, *, fallback: float = 0.99) -> None:
        self._random_values = iter(random_values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._random_values, self._fallback)


# 通用：强制不闪避 + 强制暴击的滚轮序列（足够长 60 对）
def _crit_rolls(pairs: int = 60) -> list[float]:
    out: list[float] = []
    for _ in range(pairs):
        out.append(0.99)  # dodge check 失败
        out.append(0.0)   # crit check 成功
    return out


# ─── 数据层：leifa roll keys 校验 ──────────────────────────


def test_leifa_definition_has_new_roll_keys() -> None:
    definition = get_spirit_power_definition("leifa")
    keys: set[str] = set()
    for tier_ranges in definition.roll_ranges_by_tier.values():
        for key, _low, _high in tier_ranges:
            keys.add(key)
    assert {"crit_damage_base_pct", "thunder_pct", "mark_damage_pct", "judgment_pct"} <= keys


# ─── 1. 常驻 crit_damage_base_pct 生效 ───────────────────


def test_leifa_grants_passive_crit_damage_bonus(services) -> None:
    """同样的 actor 输入下，持有 leifa 的暴击伤害应高于无 leifa 的对照。"""
    attacker_no = services.combat.create_combatant(
        name="无雷罚", atk=200, defense=10, agility=50,
    )
    attacker_yes = services.combat.create_combatant(
        name="雷罚客",
        atk=200,
        defense=10,
        agility=50,
        spirit_power=SpiritPowerEntry(
            "leifa",
            {
                "crit_damage_base_pct": 60,
                "thunder_pct": 0,
                "mark_damage_pct": 0,
                "judgment_pct": 0,
            },
        ),
    )
    defender_a = services.combat.create_combatant(name="木桩", atk=1, defense=10_000, agility=1)
    defender_b = services.combat.create_combatant(name="木桩", atk=1, defense=10_000, agility=1)

    services.combat.max_rounds = 1
    base = services.combat.run_battle(attacker_no, defender_a, rng=CombatRoller(_crit_rolls()))
    boosted = services.combat.run_battle(attacker_yes, defender_b, rng=CombatRoller(_crit_rolls()))

    base_hp = base.defender_hp_after
    boosted_hp = boosted.defender_hp_after
    assert boosted_hp < base_hp, f"雷罚常驻爆伤未生效: base={base_hp} boosted={boosted_hp}"


# ─── 2. 暴击挂雷殛（上限 3 层） ─────────────────────────


def test_leifa_crit_stacks_leihen_capped_at_three(services) -> None:
    """连续暴击命中后，目标身上「雷殛」最高不超过 3 层。"""
    attacker = services.combat.create_combatant(
        name="雷罚客",
        atk=50,
        defense=10,
        agility=50,
        spirit_power=SpiritPowerEntry(
            "leifa",
            {
                "crit_damage_base_pct": 10,
                "thunder_pct": 30,
                "mark_damage_pct": 0,
                "judgment_pct": 0,
            },
        ),
    )
    defender = services.combat.create_combatant(
        name="高韧目标", atk=1, defense=100_000, agility=10, base_resilience=36,
    )
    services.combat.max_rounds = 6
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller(_crit_rolls()))

    leihen_logs = [log for log in battle.logs if log.text and "雷殛" in log.text]
    assert leihen_logs, "应有雷殛烙印日志"
    # 没有 4/3 这种越界
    assert not any(log.text and ("(4/3)" in log.text or "4/3）" in log.text) for log in battle.logs)


# ─── 3. 雷殛下次受击触发轻量真伤（吃韧性） ───────────


def test_leihen_triggers_on_hit_truedamage_respects_resilience(services) -> None:
    """target 身上有雷殛时，受击应额外承受 mark_damage_pct%；高韧性目标该真伤被打折。"""
    attacker_snap = services.combat.create_combatant(
        name="雷罚客",
        atk=200,
        defense=10,
        agility=50,
        spirit_power=SpiritPowerEntry(
            "leifa",
            {
                "crit_damage_base_pct": 0,
                "thunder_pct": 0,
                "mark_damage_pct": 50,
                "judgment_pct": 0,
            },
        ),
    )
    target_lo = services.combat.create_combatant(name="低韧", atk=1, defense=10_000, agility=10, base_resilience=0)
    target_hi = services.combat.create_combatant(name="高韧", atk=1, defense=10_000, agility=10, base_resilience=36)

    services.combat.max_rounds = 3
    battle_lo = services.combat.run_battle(attacker_snap, target_lo, rng=CombatRoller(_crit_rolls()))
    battle_hi = services.combat.run_battle(attacker_snap, target_hi, rng=CombatRoller(_crit_rolls()))

    lo_logs = [log for log in battle_lo.logs if log.text and "雷殛在" in log.text and "炸开" in log.text]
    hi_logs = [log for log in battle_hi.logs if log.text and "雷殛在" in log.text and "炸开" in log.text]
    assert lo_logs, "低韧目标应有雷殛真伤日志"
    assert hi_logs, "高韧目标应有雷殛真伤日志"

    import re
    def first_dmg(logs):
        for log in logs:
            m = re.search(r"追加\s*([0-9]+)\s*点雷殛真伤", log.text)
            if m:
                return int(m.group(1))
        return None

    dmg_lo = first_dmg(lo_logs)
    dmg_hi = first_dmg(hi_logs)
    assert dmg_lo is not None and dmg_hi is not None
    assert dmg_hi < dmg_lo, f"高韧目标雷殛真伤未被韧性削减: lo={dmg_lo} hi={dmg_hi}"


# ─── 4. 雷殛 3 层引爆豁免韧性 ──────────────────────────


def test_leihen_judgment_burst_bypasses_resilience(services) -> None:
    """3 层雷殛引爆应造成最大生命 judgment_pct% 真伤，且豁免韧性（高韧也吃满）。"""
    attacker = services.combat.create_combatant(
        name="雷罚客",
        atk=100,
        defense=10,
        agility=50,
        spirit_power=SpiritPowerEntry(
            "leifa",
            {
                "crit_damage_base_pct": 0,
                "thunder_pct": 10,
                "mark_damage_pct": 0,
                "judgment_pct": 15,
            },
        ),
    )
    defender = services.combat.create_combatant(
        name="高韧", atk=1, defense=10_000, agility=10, base_resilience=36,
    )
    services.combat.max_rounds = 8
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller(_crit_rolls()))

    judgment_logs = [log for log in battle.logs if log.text and "雷劫真伤" in log.text]
    assert judgment_logs, "应触发雷劫引爆"

    import re
    m = re.search(r"造成\s*([0-9]+)\s*点雷劫真伤", judgment_logs[0].text)
    assert m, f"无法解析引爆伤害: {judgment_logs[0].text}"
    dmg = int(m.group(1))
    # 100_000 * 15% = 15_000，豁免韧性 → 不被 36% 削减
    assert dmg == 15_000, f"雷劫引爆未豁免韧性: {dmg} (期望 15000)"


# ─── 5. tianwei 同时挂 crit_pct 和 crit_damage_pct ─────


def test_tianwei_adds_both_crit_and_crit_damage_status(services) -> None:
    """tianwei on_crit 触发后，战报应出现「天威加身，暴击率与杀势同涨」。"""
    tianwei_affix = ArtifactAffixEntry(
        slot=1, affix_id="tianwei", rolls={"crit_pct": 5, "crit_damage_pct": 10}
    )
    attacker = services.combat.create_combatant(
        name="天威主", atk=80, defense=10, agility=50, affixes=(tianwei_affix,),
    )
    defender = services.combat.create_combatant(name="木桩", atk=1, defense=10_000, agility=1)
    services.combat.max_rounds = 1
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller(_crit_rolls()))

    assert any(log.text and "天威加身" in log.text for log in battle.logs)


def test_tianwei_status_has_both_fields() -> None:
    """直接构造 _StatusEffect 验证字段并存。"""
    eff = _StatusEffect("天威", crit_bonus_pct=5, crit_damage_pct=10)
    assert eff.crit_bonus_pct == 5
    assert eff.crit_damage_pct == 10


# ─── 6. leiyin 暴击 3 次触发小雷劫真伤 ─────────────────


def test_leiyin_triggers_burst_after_three_crits(services) -> None:
    """雷引词条改 on_crit：每次暴击挂下击增伤，并累计 3 次暴击触发 burst_pct% 最大生命真伤。"""
    leiyin_affix = ArtifactAffixEntry(
        slot=1, affix_id="leiyin", rolls={"next_damage_pct": 20, "burst_pct": 6}
    )
    attacker = services.combat.create_combatant(
        name="雷引主", atk=80, defense=10, agility=50, affixes=(leiyin_affix,),
    )
    defender = services.combat.create_combatant(
        name="桩子", atk=1, defense=100_000, agility=10,
    )
    services.combat.max_rounds = 5
    battle = services.combat.run_battle(attacker, defender, rng=CombatRoller(_crit_rolls()))

    assert any(log.text and "雷引蓄势" in log.text for log in battle.logs)
    burst_logs = [log for log in battle.logs if log.text and "雷引三激" in log.text]
    assert burst_logs, "3 次暴击后应触发雷引小雷劫"


# ─── 7. liekong 在雷殛标记下提供穿透 ──────────────────


def test_liekong_pierce_scales_with_target_leihen_layers(services) -> None:
    """liekong 应读 target 雷殛层数；无雷殛时不提供穿透。"""
    combat = CombatService()
    liekong_affix = ArtifactAffixEntry(slot=1, affix_id="liekong", rolls={"pierce_pct": 20})

    actor_snap = combat.create_combatant(
        name="裂空主", atk=80, defense=10, agility=50, affixes=(liekong_affix,),
    )
    target_snap = combat.create_combatant(name="target", atk=1, defense=10, agility=10)

    actor_state = _CombatState(snapshot=actor_snap, hp=actor_snap.max_hp)
    target_state = _CombatState(snapshot=target_snap, hp=target_snap.max_hp)

    # 无雷殛 → pierce=0
    assert combat._pierce_pct(actor_state, set(), target_state) == 0

    # 挂 2 层雷殛 → pierce 应为 20 * 2 = 40
    combat._add_target_leihen(target_state, actor_state)
    combat._add_target_leihen(target_state, actor_state)
    assert combat._pierce_pct(actor_state, set(), target_state) == 40

    combat._add_target_leihen(target_state, actor_state)
    assert combat._pierce_pct(actor_state, set(), target_state) == 60
