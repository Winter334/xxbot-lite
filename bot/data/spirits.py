from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable

RollMap = dict[str, int]


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
    roll_ranges_by_tier: dict[str, tuple[tuple[str, int, int], ...]]
    description_builder: Callable[[RollMap], str]

    def roll(self, tier_key: str, rng: random.Random) -> SpiritPowerEntry:
        ranges = self.roll_ranges_by_tier[tier_key]
        rolls = {key: rng.randint(low, high) for key, low, high in ranges}
        return SpiritPowerEntry(self.power_id, rolls)

    def describe(self, rolls: RollMap) -> str:
        return self.description_builder(rolls)


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


def _tier_rolls(
    *,
    low: tuple[tuple[str, int, int], ...],
    mid: tuple[tuple[str, int, int], ...],
    high: tuple[tuple[str, int, int], ...],
    peak: tuple[tuple[str, int, int], ...],
    supreme: tuple[tuple[str, int, int], ...],
) -> dict[str, tuple[tuple[str, int, int], ...]]:
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
    roll_ranges_by_tier: dict[str, tuple[tuple[str, int, int], ...]],
    description_builder: Callable[[RollMap], str],
) -> SpiritPowerDefinition:
    return SpiritPowerDefinition(power_id, name, roll_ranges_by_tier, description_builder)


SPIRIT_POWER_DEFINITIONS = (
    _define_power(
        "shisheng",
        "噬生",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 18, 24),),
            mid=(("heal_pct", 24, 32),),
            high=(("heal_pct", 32, 42),),
            peak=(("heal_pct", 40, 54),),
            supreme=(("heal_pct", 50, 66),),
        ),
        description_builder=lambda rolls: f"造成伤害后，按本次实际伤害的 {rolls['heal_pct']}% 回复生命；可被灼魂等附带伤害触发。",
    ),
    _define_power(
        "jueming",
        "绝命",
        roll_ranges_by_tier=_tier_rolls(
            low=(("execute_threshold_pct", 12, 16),),
            mid=(("execute_threshold_pct", 15, 20),),
            high=(("execute_threshold_pct", 18, 24),),
            peak=(("execute_threshold_pct", 22, 28),),
            supreme=(("execute_threshold_pct", 26, 34),),
        ),
        description_builder=lambda rolls: f"造成伤害后，若目标生命低于 {rolls['execute_threshold_pct']}%，则直接斩灭；可被附带伤害补刀。",
    ),
    _define_power(
        "xuanjia",
        "玄甲",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 22, 28), ("reduce_pct", 55, 70)),
            mid=(("proc_pct", 26, 34), ("reduce_pct", 65, 78)),
            high=(("proc_pct", 32, 40), ("reduce_pct", 75, 88)),
            peak=(("proc_pct", 38, 46), ("reduce_pct", 84, 96)),
            supreme=(("proc_pct", 44, 52), ("reduce_pct", 92, 100)),
        ),
        description_builder=lambda rolls: f"每回合首次受击时，有 {rolls['proc_pct']}% 概率格挡本次伤害，减伤 {rolls['reduce_pct']}%。",
    ),
    _define_power(
        "fanji",
        "反棘",
        roll_ranges_by_tier=_tier_rolls(
            low=(("reflect_pct", 20, 28),),
            mid=(("reflect_pct", 26, 36),),
            high=(("reflect_pct", 34, 46),),
            peak=(("reflect_pct", 44, 58),),
            supreme=(("reflect_pct", 56, 72),),
        ),
        description_builder=lambda rolls: f"受击后，按本次实际承伤的 {rolls['reflect_pct']}% 反弹伤害。",
    ),
    _define_power(
        "guifeng",
        "归锋",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 16, 22), ("damage_pct", 70, 85)),
            mid=(("proc_pct", 20, 28), ("damage_pct", 85, 100)),
            high=(("proc_pct", 26, 34), ("damage_pct", 100, 118)),
            peak=(("proc_pct", 32, 40), ("damage_pct", 118, 138)),
            supreme=(("proc_pct", 38, 48), ("damage_pct", 138, 160)),
        ),
        description_builder=lambda rolls: (
            f"受击后有 {rolls['proc_pct']}% 概率立刻反击 1 次，本次反击造成 {rolls['damage_pct']}% 伤害。"
        ),
    ),
    _define_power(
        "niepan",
        "涅槃",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 25, 32), ("reduce_pct", 45, 55)),
            mid=(("heal_pct", 30, 38), ("reduce_pct", 55, 65)),
            high=(("heal_pct", 36, 46), ("reduce_pct", 65, 75)),
            peak=(("heal_pct", 44, 56), ("reduce_pct", 75, 85)),
            supreme=(("heal_pct", 52, 68), ("reduce_pct", 85, 95)),
        ),
        description_builder=lambda rolls: (
            f"首次致死时不死，回复 {rolls['heal_pct']}% 最大生命，并在接下来 2 次受击时减伤 {rolls['reduce_pct']}%。"
        ),
    ),
    _define_power(
        "jinmai",
        "禁脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 10, 13),),
            mid=(("proc_pct", 12, 16),),
            high=(("proc_pct", 15, 19),),
            peak=(("proc_pct", 18, 22),),
            supreme=(("proc_pct", 22, 26),),
        ),
        description_builder=lambda rolls: f"命中后有 {rolls['proc_pct']}% 概率封脉，使目标下次行动失效。",
    ),
    _define_power(
        "xuekuang",
        "血狂",
        roll_ranges_by_tier=_tier_rolls(
            low=(
                ("per_lost_10_pct", 5, 6),
                ("max_bonus_pct", 45, 45),
                ("frenzy_bonus_pct", 20, 28),
                ("frenzy_lifesteal_pct", 8, 12),
            ),
            mid=(
                ("per_lost_10_pct", 6, 7),
                ("max_bonus_pct", 65, 65),
                ("frenzy_bonus_pct", 28, 38),
                ("frenzy_lifesteal_pct", 10, 14),
            ),
            high=(
                ("per_lost_10_pct", 8, 9),
                ("max_bonus_pct", 85, 85),
                ("frenzy_bonus_pct", 38, 50),
                ("frenzy_lifesteal_pct", 14, 18),
            ),
            peak=(
                ("per_lost_10_pct", 9, 10),
                ("max_bonus_pct", 105, 105),
                ("frenzy_bonus_pct", 50, 64),
                ("frenzy_lifesteal_pct", 18, 24),
            ),
            supreme=(
                ("per_lost_10_pct", 11, 13),
                ("max_bonus_pct", 135, 135),
                ("frenzy_bonus_pct", 64, 82),
                ("frenzy_lifesteal_pct", 22, 30),
            ),
        ),
        description_builder=lambda rolls: (
            f"每损失 10% 最大生命，伤害提高 {rolls['per_lost_10_pct']}%，最高 {rolls['max_bonus_pct']}%；"
            f"生命低于 25% 时，额外增伤 {rolls['frenzy_bonus_pct']}%，并获得 {rolls['frenzy_lifesteal_pct']}% 吸血。"
        ),
    ),
    _define_power(
        "fenmai",
        "焚脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("ignite_pct", 50, 70),),
            mid=(("ignite_pct", 65, 85),),
            high=(("ignite_pct", 80, 105),),
            peak=(("ignite_pct", 100, 130),),
            supreme=(("ignite_pct", 125, 160),),
        ),
        description_builder=lambda rolls: f"攻击命中带灼烧目标后，额外引爆本次伤害的 {rolls['ignite_pct']}%。",
    ),
    _define_power(
        "luejie",
        "戮厄",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_debuff_pct", 10, 12), ("max_bonus_pct", 30, 30)),
            mid=(("per_debuff_pct", 12, 15), ("max_bonus_pct", 40, 40)),
            high=(("per_debuff_pct", 15, 18), ("max_bonus_pct", 52, 52)),
            peak=(("per_debuff_pct", 18, 22), ("max_bonus_pct", 66, 66)),
            supreme=(("per_debuff_pct", 22, 28), ("max_bonus_pct", 82, 82)),
        ),
        description_builder=lambda rolls: (
            f"目标每带 1 个减益，本次伤害提高 {rolls['per_debuff_pct']}%，最高 {rolls['max_bonus_pct']}%。"
        ),
    ),
    _define_power(
        "chengshi",
        "乘势",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 18, 24),),
            mid=(("damage_pct", 24, 32),),
            high=(("damage_pct", 32, 42),),
            peak=(("damage_pct", 42, 54),),
            supreme=(("damage_pct", 54, 68),),
        ),
        description_builder=lambda rolls: f"若本次出手已受追命、断岳、镇关等加持，则额外增伤 {rolls['damage_pct']}%。",
    ),
    _define_power(
        "lingyong",
        "灵涌",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_buff_pct", 6, 8), ("max_bonus_pct", 20, 20)),
            mid=(("per_buff_pct", 8, 10), ("max_bonus_pct", 30, 30)),
            high=(("per_buff_pct", 10, 13), ("max_bonus_pct", 42, 42)),
            peak=(("per_buff_pct", 13, 16), ("max_bonus_pct", 56, 56)),
            supreme=(("per_buff_pct", 16, 20), ("max_bonus_pct", 72, 72)),
        ),
        description_builder=lambda rolls: (
            f"自身每带 1 个增益，本次伤害提高 {rolls['per_buff_pct']}%，最高 {rolls['max_bonus_pct']}%。"
        ),
    ),
    _define_power(
        "zhuying",
        "逐影",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 16, 22), ("extra_pct", 8, 12)),
            mid=(("damage_pct", 22, 28), ("extra_pct", 10, 14)),
            high=(("damage_pct", 28, 36), ("extra_pct", 14, 18)),
            peak=(("damage_pct", 36, 44), ("extra_pct", 18, 24)),
            supreme=(("damage_pct", 44, 56), ("extra_pct", 24, 30)),
        ),
        description_builder=lambda rolls: (
            f"若自身当前身法高于目标，则本次伤害提高 {rolls['damage_pct']}%；"
            f"若高出至少 25%，则额外再提高 {rolls['extra_pct']}%。"
        ),
    ),
    _define_power(
        "huajing",
        "化劲",
        roll_ranges_by_tier=_tier_rolls(
            low=(("convert_pct", 28, 38),),
            mid=(("convert_pct", 38, 48),),
            high=(("convert_pct", 48, 60),),
            peak=(("convert_pct", 60, 74),),
            supreme=(("convert_pct", 74, 90),),
        ),
        description_builder=lambda rolls: f"若自身带有减伤护体，受击后按本次承伤的 {rolls['convert_pct']}% 回血。",
    ),
    _define_power(
        "duofeng",
        "夺锋",
        roll_ranges_by_tier=_tier_rolls(
            low=(("atk_pct", 8, 10), ("agi_pct", 8, 10)),
            mid=(("atk_pct", 10, 12), ("agi_pct", 10, 12)),
            high=(("atk_pct", 12, 15), ("agi_pct", 12, 15)),
            peak=(("atk_pct", 15, 18), ("agi_pct", 15, 18)),
            supreme=(("atk_pct", 18, 22), ("agi_pct", 18, 22)),
        ),
        description_builder=lambda rolls: (
            f"命中带减益目标后，夺其锋芒 2 回合；自身杀伐、身法各提高 {rolls['atk_pct']}% / {rolls['agi_pct']}%，"
            f"目标则等额降低。"
        ),
    ),
    _define_power(
        "zhenling",
        "镇灵",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 4, 6),),
            mid=(("heal_pct", 6, 8),),
            high=(("heal_pct", 8, 10),),
            peak=(("heal_pct", 10, 13),),
            supreme=(("heal_pct", 13, 16),),
        ),
        description_builder=lambda rolls: f"每回合首次受创时，若自身带减益，则净去 1 个减益并回复 {rolls['heal_pct']}% 最大生命。",
    ),
)
SPIRIT_POWER_BY_ID = {definition.power_id: definition for definition in SPIRIT_POWER_DEFINITIONS}


def get_spirit_power_definition(power_id: str) -> SpiritPowerDefinition:
    return SPIRIT_POWER_BY_ID[power_id]
