from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable, Mapping

RollValue = int | float
RollMap = Mapping[str, RollValue]


@dataclass(frozen=True, slots=True)
class SpiritStatEntry:
    stat: str
    kind: str
    value: int

    def to_payload(self) -> dict[str, int | str]:
        return {"stat": self.stat, "kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class SpiritPowerEntry:
    power_id: str
    rolls: RollMap

    def to_payload(self) -> dict[str, object]:
        return {"power_id": self.power_id, "rolls": dict(self.rolls)}


@dataclass(frozen=True, slots=True)
class SpiritInstance:
    tier: str
    stats: tuple[SpiritStatEntry, ...]
    power: SpiritPowerEntry

    def to_payload(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "stats": [entry.to_payload() for entry in self.stats],
            "power": self.power.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class SpiritTierDefinition:
    key: str
    name: str
    weight: int
    flat_range: tuple[int, int]
    ratio_range: tuple[int, int]


@dataclass(frozen=True, slots=True)
class SpiritPowerDefinition:
    power_id: str
    name: str
    roll_ranges_by_tier: dict[str, tuple[tuple[str, RollValue, RollValue], ...]]
    description_builder: Callable[[RollMap], str]

    def roll(self, tier_key: str, rng: random.Random) -> SpiritPowerEntry:
        ranges = self.roll_ranges_by_tier[tier_key]
        rolls: dict[str, RollValue] = {}
        for key, low, high in ranges:
            if isinstance(low, float) or isinstance(high, float):
                rolls[key] = round(rng.uniform(float(low), float(high)), 1)
            else:
                rolls[key] = rng.randint(low, high)
        return SpiritPowerEntry(self.power_id, rolls)

    def describe(self, rolls: RollMap) -> str:
        return self.description_builder(self.normalize_rolls(rolls))

    def normalize_rolls(self, rolls: RollMap) -> dict[str, RollValue]:
        normalized = dict(rolls)
        for ranges in self.roll_ranges_by_tier.values():
            for key, low, _high in ranges:
                normalized.setdefault(key, low)
        return normalized


SPIRIT_NAMES = (
    "玄乌",
    "青霄",
    "离焰",
    "寒汐",
    "照夜",
    "归砂",
    "断潮",
    "苍岚",
    "沉星",
    "白螭",
    "赤羽",
    "玄铃",
)

SPIRIT_TIER_DEFINITIONS = (
    SpiritTierDefinition("low", "下品", 45, (18, 28), (18, 26)),
    SpiritTierDefinition("mid", "中品", 30, (26, 40), (26, 38)),
    SpiritTierDefinition("high", "上品", 17, (38, 58), (36, 54)),
    SpiritTierDefinition("peak", "极品", 6, (52, 78), (48, 72)),
    SpiritTierDefinition("supreme", "绝品", 2, (70, 105), (64, 98)),
)
SPIRIT_TIER_BY_KEY = {definition.key: definition for definition in SPIRIT_TIER_DEFINITIONS}
SPIRIT_TIER_ORDER = ("low", "mid", "high", "peak", "supreme")


def _tier_rolls(
    *,
    low: tuple[tuple[str, RollValue, RollValue], ...],
    mid: tuple[tuple[str, RollValue, RollValue], ...],
    high: tuple[tuple[str, RollValue, RollValue], ...],
    peak: tuple[tuple[str, RollValue, RollValue], ...],
    supreme: tuple[tuple[str, RollValue, RollValue], ...],
) -> dict[str, tuple[tuple[str, RollValue, RollValue], ...]]:
    return {
        "low": low,
        "mid": mid,
        "high": high,
        "peak": peak,
        "supreme": supreme,
    }


def _define_power(
    power_id: str,
    name: str,
    *,
    roll_ranges_by_tier: dict[str, tuple[tuple[str, RollValue, RollValue], ...]],
    description_builder: Callable[[RollMap], str],
) -> SpiritPowerDefinition:
    return SpiritPowerDefinition(power_id, name, roll_ranges_by_tier, description_builder)


SPIRIT_POWER_DEFINITIONS = (
    _define_power(
        "shisheng",
        "噬生",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 20, 28),),
            mid=(("heal_pct", 28, 38),),
            high=(("heal_pct", 38, 50),),
            peak=(("heal_pct", 50, 62),),
            supreme=(("heal_pct", 60, 75),),
        ),
        description_builder=lambda rolls: f"普通攻击与自身造成的灼烧造成实际伤害后，按伤害的 {rolls['heal_pct']}% 回复生命。",
    ),
    _define_power(
        "jueming",
        "绝命",
        roll_ranges_by_tier=_tier_rolls(
            low=(("omen_cost", 8, 8), ("execute_pct", 18, 18), ("heal_down_pct", 35, 35)),
            mid=(("omen_cost", 7, 7), ("execute_pct", 22, 22), ("heal_down_pct", 40, 40)),
            high=(("omen_cost", 6, 6), ("execute_pct", 26, 26), ("heal_down_pct", 45, 45)),
            peak=(("omen_cost", 5, 5), ("execute_pct", 30, 30), ("heal_down_pct", 50, 50)),
            supreme=(("omen_cost", 4, 4), ("execute_pct", 35, 35), ("heal_down_pct", 55, 55)),
        ),
        description_builder=lambda rolls: (
            f"回合结束时，若目标咒印≥{rolls['omen_cost']}层，消耗{rolls['omen_cost']}层咒印凝成 1 层死兆；"
            f"每层死兆使目标受疗降低 {rolls['heal_down_pct']}%，并提高 {rolls['execute_pct']}% 斩杀线；死兆 3 层时直接斩杀。"
        ),
    ),
    _define_power(
        "xuanjia",
        "玄甲",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 25, 32), ("reduce_pct", 65, 75)),
            mid=(("proc_pct", 30, 38), ("reduce_pct", 72, 82)),
            high=(("proc_pct", 36, 45), ("reduce_pct", 80, 90)),
            peak=(("proc_pct", 42, 52), ("reduce_pct", 88, 96)),
            supreme=(("proc_pct", 50, 60), ("reduce_pct", 94, 100)),
        ),
        description_builder=lambda rolls: f"每回合首次受击时，有 {rolls['proc_pct']}% 概率格挡本次伤害，减伤 {rolls['reduce_pct']}%；守势会提高概率。",
    ),
    _define_power(
        "fanji",
        "反棘",
        roll_ranges_by_tier=_tier_rolls(
            low=(("reflect_pct", 25, 32),),
            mid=(("reflect_pct", 32, 42),),
            high=(("reflect_pct", 42, 55),),
            peak=(("reflect_pct", 55, 70),),
            supreme=(("reflect_pct", 70, 90),),
        ),
        description_builder=lambda rolls: f"受击后，按本次实际承伤的 {rolls['reflect_pct']}% 反弹伤害；有守势或减伤时反伤比例额外 +20 个百分点。",
    ),
    _define_power(
        "guifeng",
        "归锋",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 20, 26), ("damage_pct", 80, 95)),
            mid=(("proc_pct", 24, 32), ("damage_pct", 95, 112)),
            high=(("proc_pct", 30, 40), ("damage_pct", 110, 132)),
            peak=(("proc_pct", 38, 48), ("damage_pct", 130, 155)),
            supreme=(("proc_pct", 46, 58), ("damage_pct", 150, 180)),
        ),
        description_builder=lambda rolls: f"受击后有 {rolls['proc_pct']}% 概率立刻反击 1 次，本次反击造成 {rolls['damage_pct']}% 伤害；血线落后触发率 +15 个百分点；每回合最多一次。",
    ),
    _define_power(
        "niepan",
        "涅槃",
        roll_ranges_by_tier=_tier_rolls(
            low=(("revive_hp_pct", 28, 36), ("cost_stacks", 12, 12), ("per_revive_atk_pct", 6, 10), ("per_revive_speed_pct", 5, 9), ("revive_shield_pct", 15, 20)),
            mid=(("revive_hp_pct", 35, 44), ("cost_stacks", 10, 10), ("per_revive_atk_pct", 8, 13), ("per_revive_speed_pct", 7, 11), ("revive_shield_pct", 20, 26)),
            high=(("revive_hp_pct", 44, 55), ("cost_stacks", 8, 8), ("per_revive_atk_pct", 12, 17), ("per_revive_speed_pct", 10, 15), ("revive_shield_pct", 26, 32)),
            peak=(("revive_hp_pct", 55, 66), ("cost_stacks", 7, 7), ("per_revive_atk_pct", 15, 21), ("per_revive_speed_pct", 12, 18), ("revive_shield_pct", 32, 40)),
            supreme=(("revive_hp_pct", 66, 80), ("cost_stacks", 6, 6), ("per_revive_atk_pct", 19, 26), ("per_revive_speed_pct", 16, 22), ("revive_shield_pct", 40, 50)),
        ),
        description_builder=lambda rolls: (
            f"濒死时若生息层数 ≥ {rolls.get('cost_stacks', 12)}，消耗 {rolls.get('cost_stacks', 12)} 层生息复活，"
            f"回复 {rolls.get('revive_hp_pct', rolls.get('heal_pct', 20))}% 最大生命；每次复活后永久提高 "
            f"{rolls.get('per_revive_atk_pct', 5)}% 杀伐与 {rolls.get('per_revive_speed_pct', 4)}% 身法（可叠加）；"
            f"并获得最大生命 {rolls.get('revive_shield_pct', 0)}% 的余烬护盾。"
        ),
    ),
    _define_power(
        "jinmai",
        "禁脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 25, 35), ("per_disrupt_pct", 6, 6), ("seal_stacks", 1, 1), ("break_pobu_stacks", 1, 1), ("break_wound_stacks", 0, 0), ("break_strip_stacks", 0, 0)),
            mid=(("proc_pct", 35, 45), ("per_disrupt_pct", 7, 7), ("seal_stacks", 1, 1), ("break_pobu_stacks", 1, 1), ("break_wound_stacks", 1, 1), ("break_strip_stacks", 0, 0)),
            high=(("proc_pct", 45, 55), ("per_disrupt_pct", 8, 8), ("seal_stacks", 1, 2), ("break_pobu_stacks", 2, 2), ("break_wound_stacks", 1, 1), ("break_strip_stacks", 0, 0)),
            peak=(("proc_pct", 55, 70), ("per_disrupt_pct", 9, 9), ("seal_stacks", 2, 2), ("break_pobu_stacks", 2, 2), ("break_wound_stacks", 2, 2), ("break_strip_stacks", 1, 1)),
            supreme=(("proc_pct", 70, 85), ("per_disrupt_pct", 10, 10), ("seal_stacks", 2, 3), ("break_pobu_stacks", 3, 3), ("break_wound_stacks", 2, 2), ("break_strip_stacks", 1, 2)),
        ),
        description_builder=lambda rolls: (
            f"命中时扰乱经络：先铺破步/创伤，再以 {rolls['proc_pct']}% 基础概率封禁行动；"
            f"目标每层破步/创伤使概率 +{rolls['per_disrupt_pct']}%。成功附加 {rolls['seal_stacks']} 层封禁行动；"
            f"封禁触发时断脉，追加破步 {rolls['break_pobu_stacks']} 层、创伤 {rolls['break_wound_stacks']} 层。"
        ),
    ),
    _define_power(
        "xuekuang",
        "血狂",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_lost_10_pct", 8, 10), ("max_bonus_pct", 80, 80), ("frenzy_lifesteal_pct", 8, 12)),
            mid=(("per_lost_10_pct", 10, 12), ("max_bonus_pct", 105, 105), ("frenzy_lifesteal_pct", 10, 14)),
            high=(("per_lost_10_pct", 12, 15), ("max_bonus_pct", 130, 130), ("frenzy_lifesteal_pct", 12, 18)),
            peak=(("per_lost_10_pct", 15, 18), ("max_bonus_pct", 155, 155), ("frenzy_lifesteal_pct", 16, 22)),
            supreme=(("per_lost_10_pct", 18, 22), ("max_bonus_pct", 180, 180), ("frenzy_lifesteal_pct", 20, 28)),
        ),
        description_builder=lambda rolls: f"每损失 10% 最大生命，伤害提高 {rolls['per_lost_10_pct']}%，最高 {rolls['max_bonus_pct']}%；生命不高于 25% 时获得 {rolls['frenzy_lifesteal_pct']}% 吸血。",
    ),
    _define_power(
        "fenmai",
        "焚脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_burn_pct", 0.5, 0.8),),
            mid=(("per_burn_pct", 0.8, 1.2),),
            high=(("per_burn_pct", 1.2, 1.6),),
            peak=(("per_burn_pct", 1.5, 1.8),),
            supreme=(("per_burn_pct", 1.8, 2.0),),
        ),
        description_builder=lambda rolls: (
            f"命中灼烧目标时，附加目标最大生命 {rolls['per_burn_pct']}% × 灼烧层数 的伤害（无上限）。"
        ),
    ),
    _define_power(
        "luejie",
        "戮厄",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_debuff_pct", 12, 15), ("max_bonus_pct", 120, 120)),
            mid=(("per_debuff_pct", 15, 20), ("max_bonus_pct", 160, 160)),
            high=(("per_debuff_pct", 20, 24), ("max_bonus_pct", 200, 200)),
            peak=(("per_debuff_pct", 24, 28), ("max_bonus_pct", 250, 250)),
            supreme=(("per_debuff_pct", 28, 34), ("max_bonus_pct", 300, 300)),
        ),
        description_builder=lambda rolls: (
            f"目标每有 1 层负面效果，普通攻击伤害提高 {rolls['per_debuff_pct']}%，最高 {rolls['max_bonus_pct']}%；"
            f"攻击结算时目标仍存活且负面效果不少于 5 层，追加当前杀伐 × {rolls['per_debuff_pct']}% 的伤害。"
        ),
    ),
    _define_power(
        "chengshi",
        "乘势",
        roll_ranges_by_tier=_tier_rolls(
            low=(("base_pct", 20, 28), ("per_type_pct", 15, 20)),
            mid=(("base_pct", 28, 38), ("per_type_pct", 20, 26)),
            high=(("base_pct", 38, 50), ("per_type_pct", 26, 34)),
            peak=(("base_pct", 50, 64), ("per_type_pct", 34, 42)),
            supreme=(("base_pct", 64, 80), ("per_type_pct", 42, 52)),
        ),
        description_builder=lambda rolls: f"持有 2 个以上增伤类法宝词条时，伤害提高 {rolls['base_pct']}%；第 3 个起每多 1 个额外提高 {rolls['per_type_pct']}%。",
    ),
    _define_power(
        "lingyong",
        "灵涌",
        roll_ranges_by_tier=_tier_rolls(
            low=(("start_stacks", 1, 1), ("per_stack_pct", 2, 3)),
            mid=(("start_stacks", 2, 2), ("per_stack_pct", 3, 5)),
            high=(("start_stacks", 3, 3), ("per_stack_pct", 5, 7)),
            peak=(("start_stacks", 4, 4), ("per_stack_pct", 7, 10)),
            supreme=(("start_stacks", 5, 5), ("per_stack_pct", 10, 14)),
        ),
        description_builder=lambda rolls: (
            f"战斗开始时获得 {rolls['start_stacks']} 层灵势；"
            f"自身每层灵势额外提高造成伤害 {rolls['per_stack_pct']}%（与聚灵词条联动加速爆发）。"
        ),
    ),
    _define_power(
        "lingyu",
        "灵御",
        roll_ranges_by_tier=_tier_rolls(
            low=(("reduce_per_stack_pct", 8, 10), ("self_damage_down_per_stack_pct", 5, 6)),
            mid=(("reduce_per_stack_pct", 10, 12), ("self_damage_down_per_stack_pct", 5, 7)),
            high=(("reduce_per_stack_pct", 12, 15), ("self_damage_down_per_stack_pct", 6, 9)),
            peak=(("reduce_per_stack_pct", 15, 18), ("self_damage_down_per_stack_pct", 8, 10)),
            supreme=(("reduce_per_stack_pct", 18, 22), ("self_damage_down_per_stack_pct", 10, 12)),
        ),
        description_builder=lambda rolls: (
            f"战斗前 6 回合，每层灵势提高减伤 {rolls['reduce_per_stack_pct']}% 并降低自身造成伤害 {rolls['self_damage_down_per_stack_pct']}%；"
            f"第 7 回合起效果消失，进入聚灵爆发期。"
        ),
    ),
    _define_power(
        "zhuying",
        "逐影",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 25, 35), ("per_25_pct", 10, 14), ("max_bonus_pct", 80, 80)),
            mid=(("damage_pct", 35, 48), ("per_25_pct", 14, 18), ("max_bonus_pct", 105, 105)),
            high=(("damage_pct", 48, 62), ("per_25_pct", 18, 24), ("max_bonus_pct", 130, 130)),
            peak=(("damage_pct", 62, 78), ("per_25_pct", 24, 30), ("max_bonus_pct", 155, 155)),
            supreme=(("damage_pct", 78, 95), ("per_25_pct", 30, 38), ("max_bonus_pct", 180, 180)),
        ),
        description_builder=lambda rolls: f"自身身法高于目标时增伤 {rolls['damage_pct']}%；每高出 25% 额外提高 {rolls['per_25_pct']}%，最高 {rolls['max_bonus_pct']}%。",
    ),
    _define_power(
        "huajing",
        "化劲",
        roll_ranges_by_tier=_tier_rolls(
            low=(("convert_pct", 35, 45),),
            mid=(("convert_pct", 45, 58),),
            high=(("convert_pct", 58, 72),),
            peak=(("convert_pct", 72, 88),),
            supreme=(("convert_pct", 88, 105),),
        ),
        description_builder=lambda rolls: f"若自身带有守势或减伤，受击后按本次承伤的 {rolls['convert_pct']}% 回血；每回合最多一次。",
    ),
    _define_power(
        "duofeng",
        "夺锋",
        roll_ranges_by_tier=_tier_rolls(
            low=(("atk_pct", 5, 8), ("agi_pct", 5, 8)),
            mid=(("atk_pct", 8, 11), ("agi_pct", 8, 11)),
            high=(("atk_pct", 10, 14), ("agi_pct", 10, 14)),
            peak=(("atk_pct", 12, 16), ("agi_pct", 12, 16)),
            supreme=(("atk_pct", 14, 18), ("agi_pct", 14, 18)),
        ),
        description_builder=lambda rolls: f"命中带负面层数目标后，偷取杀伐、身法各 {rolls['atk_pct']}% / {rolls['agi_pct']}%，整场生效，最多 5 层。",
    ),
    _define_power(
        "dishi",
        "涤世",
        roll_ranges_by_tier=_tier_rolls(
            low=(("threshold", 10, 14), ("stack_pct", 5, 9)),
            mid=(("threshold", 8, 12), ("stack_pct", 7, 11)),
            high=(("threshold", 6, 10), ("stack_pct", 9, 14)),
            peak=(("threshold", 5, 8), ("stack_pct", 12, 17)),
            supreme=(("threshold", 4, 7), ("stack_pct", 15, 21)),
        ),
        description_builder=lambda rolls: f"回合结束时，若全场可净化效果总层数 ≥ {rolls['threshold']}，清除全场所有可净化效果，将净化之力存储为 {rolls['stack_pct']}% × 实际清除层数杀伐的追打，下次攻击命中时释放。触发后有 1 回合冷却。",
    ),
    _define_power(
        "chunsheng",
        "春生",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_received_pct", 25, 40), ("convert_pct", 30, 45), ("heal_shengxi_bonus", 1, 1)),
            mid=(("heal_received_pct", 38, 55), ("convert_pct", 42, 58), ("heal_shengxi_bonus", 1, 1)),
            high=(("heal_received_pct", 52, 70), ("convert_pct", 55, 72), ("heal_shengxi_bonus", 1, 2)),
            peak=(("heal_received_pct", 65, 85), ("convert_pct", 68, 88), ("heal_shengxi_bonus", 1, 2)),
            supreme=(("heal_received_pct", 80, 100), ("convert_pct", 80, 105), ("heal_shengxi_bonus", 2, 2)),
        ),
        description_builder=lambda rolls: (
            f"自身受到的治疗提高 {rolls['heal_received_pct']}%；治疗后按治疗量的 {rolls['convert_pct']}% 转化为下次攻击的固定追击伤害；"
            f"每次受到治疗时额外叠加 {rolls['heal_shengxi_bonus']} 层生息（不计入护元上限）。"
        ),
    ),
    _define_power(
        "suijue",
        "碎阙",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 80, 115), ("stacks", 2, 2)),
            mid=(("damage_pct", 110, 150), ("stacks", 2, 3)),
            high=(("damage_pct", 140, 185), ("stacks", 2, 3)),
            peak=(("damage_pct", 175, 225), ("stacks", 3, 4)),
            supreme=(("damage_pct", 210, 270), ("stacks", 3, 4)),
        ),
        description_builder=lambda rolls: (
            f"命中带守势或多个正面效果的目标时，必定追加 {rolls['damage_pct']}% 伤害，并打散 {rolls['stacks']} 层正面状态；"
            f"每打散 1 层，自身获得 {rolls['damage_pct'] // 2}% 下一击增伤。"
        ),
    ),
    _define_power(
        "qiedao",
        "窃道",
        roll_ranges_by_tier=_tier_rolls(
            low=(("chain_pct", 18, 28),),
            mid=(("chain_pct", 26, 40),),
            high=(("chain_pct", 38, 54),),
            peak=(("chain_pct", 50, 68),),
            supreme=(("chain_pct", 62, 80),),
        ),
        description_builder=lambda rolls: f"回合开始自动执行：优先窃取敌方 1 个正面效果（转移至自身）；敌方无正面时改为转移自身 1 个负面给敌方。成功后 {rolls['chain_pct']}% 概率再次触发（无限链式，无上限）。",
    ),
    _define_power(
        "zhuifeng",
        "追风",
        roll_ranges_by_tier=_tier_rolls(
            low=(("r1_damage_pct", 110, 150), ("r23_damage_pct", 65, 85), ("r1_crit_bonus", 25, 25), ("r23_crit_bonus", 12, 12), ("r1_agility_pct", 15, 15), ("r23_agility_pct", 10, 10), ("shield_damage_pct", 40, 40), ("r1_pierce_pct", 15, 15), ("r23_pierce_pct", 8, 8), ("hit_heal_down_pct", 20, 20), ("crit_heal_down_pct", 30, 30), ("chase_damage_pct", 6, 6)),
            mid=(("r1_damage_pct", 150, 200), ("r23_damage_pct", 85, 115), ("r1_crit_bonus", 35, 35), ("r23_crit_bonus", 17, 17), ("r1_agility_pct", 20, 20), ("r23_agility_pct", 12, 12), ("shield_damage_pct", 60, 60), ("r1_pierce_pct", 20, 20), ("r23_pierce_pct", 10, 10), ("hit_heal_down_pct", 28, 28), ("crit_heal_down_pct", 40, 40), ("chase_damage_pct", 8, 8)),
            high=(("r1_damage_pct", 200, 270), ("r23_damage_pct", 115, 155), ("r1_crit_bonus", 50, 50), ("r23_crit_bonus", 25, 25), ("r1_agility_pct", 28, 28), ("r23_agility_pct", 18, 18), ("shield_damage_pct", 80, 80), ("r1_pierce_pct", 28, 28), ("r23_pierce_pct", 14, 14), ("hit_heal_down_pct", 36, 36), ("crit_heal_down_pct", 50, 50), ("chase_damage_pct", 10, 10)),
            peak=(("r1_damage_pct", 270, 360), ("r23_damage_pct", 155, 210), ("r1_crit_bonus", 65, 65), ("r23_crit_bonus", 32, 32), ("r1_agility_pct", 36, 36), ("r23_agility_pct", 24, 24), ("shield_damage_pct", 110, 110), ("r1_pierce_pct", 36, 36), ("r23_pierce_pct", 18, 18), ("hit_heal_down_pct", 45, 45), ("crit_heal_down_pct", 60, 60), ("chase_damage_pct", 13, 13)),
            supreme=(("r1_damage_pct", 360, 480), ("r23_damage_pct", 210, 280), ("r1_crit_bonus", 100, 100), ("r23_crit_bonus", 50, 50), ("r1_agility_pct", 50, 50), ("r23_agility_pct", 32, 32), ("shield_damage_pct", 150, 150), ("r1_pierce_pct", 50, 50), ("r23_pierce_pct", 25, 25), ("hit_heal_down_pct", 55, 55), ("crit_heal_down_pct", 75, 75), ("chase_damage_pct", 16, 16)),
        ),
        description_builder=lambda rolls: (
            f"先手开局进入 3 回合疾猎：首击必中，首回合伤害 +{rolls['r1_damage_pct']}%、暴击率 +{rolls['r1_crit_bonus']}%、身法 +{rolls['r1_agility_pct']}%；"
            f"第 2-3 回合保留伤害 +{rolls['r23_damage_pct']}%、暴击率 +{rolls['r23_crit_bonus']}%。"
            f"疾猎期间破盾、穿透并施加断息；暴击获得追猎，对低血目标每层增伤 {rolls['chase_damage_pct']}%。"
        ),
    ),
    # ── 新增神通 ──────────────────────────────────────────────
    _define_power(
        "leifa",
        "雷罚",
        roll_ranges_by_tier=_tier_rolls(
            low=(
                ("crit_damage_base_pct", 18, 24),
                ("charge_crit_pct", 8, 8),
                ("mark_crit_pct", 6, 6),
                ("mark_crit_damage_pct", 8, 8),
                ("thunder_pct", 80, 120),
                ("mark_damage_pct", 10, 14),
                ("judgment_pct", 18, 24),
                ("wound_stacks", 1, 1),
                ("strip_stacks", 1, 1),
                ("crit_mark_stacks", 1, 1),
                ("retain_mark_after_judgment", 0, 0),
            ),
            mid=(
                ("crit_damage_base_pct", 24, 32),
                ("charge_crit_pct", 10, 10),
                ("mark_crit_pct", 8, 8),
                ("mark_crit_damage_pct", 10, 10),
                ("thunder_pct", 120, 170),
                ("mark_damage_pct", 14, 18),
                ("judgment_pct", 24, 30),
                ("wound_stacks", 1, 1),
                ("strip_stacks", 1, 1),
                ("crit_mark_stacks", 1, 1),
                ("retain_mark_after_judgment", 0, 0),
            ),
            high=(
                ("crit_damage_base_pct", 32, 42),
                ("charge_crit_pct", 12, 12),
                ("mark_crit_pct", 10, 10),
                ("mark_crit_damage_pct", 12, 12),
                ("thunder_pct", 170, 240),
                ("mark_damage_pct", 18, 24),
                ("judgment_pct", 30, 38),
                ("wound_stacks", 2, 2),
                ("strip_stacks", 1, 2),
                ("crit_mark_stacks", 1, 1),
                ("retain_mark_after_judgment", 0, 0),
            ),
            peak=(
                ("crit_damage_base_pct", 42, 54),
                ("charge_crit_pct", 15, 15),
                ("mark_crit_pct", 12, 12),
                ("mark_crit_damage_pct", 15, 15),
                ("thunder_pct", 240, 330),
                ("mark_damage_pct", 24, 32),
                ("judgment_pct", 38, 48),
                ("wound_stacks", 3, 3),
                ("strip_stacks", 2, 2),
                ("crit_mark_stacks", 2, 2),
                ("retain_mark_after_judgment", 1, 1),
            ),
            supreme=(
                ("crit_damage_base_pct", 54, 68),
                ("charge_crit_pct", 20, 20),
                ("mark_crit_pct", 15, 15),
                ("mark_crit_damage_pct", 20, 20),
                ("thunder_pct", 330, 450),
                ("mark_damage_pct", 32, 42),
                ("judgment_pct", 50, 65),
                ("wound_stacks", 4, 4),
                ("strip_stacks", 2, 3),
                ("crit_mark_stacks", 2, 2),
                ("retain_mark_after_judgment", 1, 1),
            ),
        ),
        description_builder=lambda rolls: (
            f"常驻 +{rolls['crit_damage_base_pct']}% 暴击伤害。未暴击命中时蓄 1 层引雷（每层 +{rolls['charge_crit_pct']}% 暴击率）；"
            f"目标每层雷殛使暴击率 +{rolls['mark_crit_pct']}%、暴伤 +{rolls['mark_crit_damage_pct']}%。"
            f"暴击追加 {rolls['thunder_pct']}% 杀伐雷伤并烙 {rolls['crit_mark_stacks']} 层雷殛；3 层雷殛引动天劫，造成最大生命 {rolls['judgment_pct']}% 真伤，附加创伤并打散正面。"
        ),
    ),
    _define_power(
        "shiyan",
        "蚀焰",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_burn_pct", 12, 22), ("wound_stacks", 1, 1)),
            mid=(("per_burn_pct", 18, 30), ("wound_stacks", 2, 2)),
            high=(("per_burn_pct", 22, 38), ("wound_stacks", 3, 3)),
            peak=(("per_burn_pct", 28, 42), ("wound_stacks", 4, 4)),
            supreme=(("per_burn_pct", 32, 44), ("wound_stacks", 5, 5)),
        ),
        description_builder=lambda rolls: (
            f"命中且目标灼烧 ≥6 层时触发：消耗目标至多 10 层灼烧，每层造成 {rolls['per_burn_pct']}% 杀伐神通伤害（可被护盾抵挡），"
            f"引爆后给目标附加 {rolls['wound_stacks']} 层创伤；触发后冷却 1 回合。"
        ),
    ),
    _define_power(
        "fengdun",
        "风遁",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_wind_pct", 12, 16), ("agi_boost_pct", 8, 11)),
            mid=(("per_wind_pct", 16, 22), ("agi_boost_pct", 11, 15)),
            high=(("per_wind_pct", 22, 30), ("agi_boost_pct", 15, 20)),
            peak=(("per_wind_pct", 30, 40), ("agi_boost_pct", 20, 26)),
            supreme=(("per_wind_pct", 40, 52), ("agi_boost_pct", 26, 34)),
        ),
        description_builder=lambda rolls: (
            f"闪避后叠加 1 层风遁（上限 8）；每层提高伤害 {rolls['per_wind_pct']}% 并提升身法 {rolls['agi_boost_pct']}%；"
            f"受击命中时仅消散 1 层；满 5 层时下次攻击必定暴击且伤害额外 +50%。"
        ),
    ),
    _define_power(
        "wanzhou",
        "万咒",
        roll_ranges_by_tier=_tier_rolls(
            low=(("curse_on_hit", 1, 1), ("extra_curse_pct", 0, 0), ("burst_threshold", 3, 3), ("debuff_rolls_per_curse", 2, 2), ("seal_weight", 10, 10)),
            mid=(("curse_on_hit", 1, 1), ("extra_curse_pct", 50, 50), ("burst_threshold", 3, 3), ("debuff_rolls_per_curse", 3, 3), ("seal_weight", 10, 10)),
            high=(("curse_on_hit", 2, 2), ("extra_curse_pct", 0, 0), ("burst_threshold", 4, 4), ("debuff_rolls_per_curse", 4, 4), ("seal_weight", 10, 10)),
            peak=(("curse_on_hit", 2, 2), ("extra_curse_pct", 50, 50), ("burst_threshold", 4, 4), ("debuff_rolls_per_curse", 5, 5), ("seal_weight", 12, 12)),
            supreme=(("curse_on_hit", 3, 3), ("extra_curse_pct", 0, 0), ("burst_threshold", 5, 5), ("debuff_rolls_per_curse", 6, 6), ("seal_weight", 12, 12)),
        ),
        description_builder=lambda rolls: (
            f"命中附加 {rolls['curse_on_hit']} 层咒印，{rolls['extra_curse_pct']}% 概率额外 +1 层；"
            f"目标咒印 ≥ {rolls['burst_threshold']} 层时消耗全部咒印，每层咒印进行 {rolls['debuff_rolls_per_curse']} 次万咒判定，"
            f"随机附加灼烧、蔓咒、破步、创伤、咒缚或封禁行动。"
        ),
    ),
)
SPIRIT_POWER_BY_ID = {definition.power_id: definition for definition in SPIRIT_POWER_DEFINITIONS}


def get_spirit_power_definition(power_id: str) -> SpiritPowerDefinition:
    return SPIRIT_POWER_BY_ID[power_id]


# 器灵品阶淬炼（升阶）所需器魂消耗，key 为目标品阶
SPIRIT_TIER_UPGRADE_COSTS: dict[str, int] = {
    "mid": 80,        # low -> mid
    "high": 200,      # mid -> high
    "peak": 450,      # high -> peak
    "supreme": 1000,  # peak -> supreme
}


def get_spirit_tier_upgrade_cost(target_tier_key: str) -> int | None:
    """返回升级到 target_tier_key 所需的器魂数；非法 tier 返回 None。"""
    return SPIRIT_TIER_UPGRADE_COSTS.get(target_tier_key)


def get_next_spirit_tier(current_tier_key: str) -> str | None:
    """返回 current_tier_key 的下一阶，绝品或非法 tier 返回 None。"""
    try:
        idx = SPIRIT_TIER_ORDER.index(current_tier_key)
    except ValueError:
        return None
    if idx + 1 >= len(SPIRIT_TIER_ORDER):
        return None
    return SPIRIT_TIER_ORDER[idx + 1]
