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
            low=(("heal_pct", 8, 12),),
            mid=(("heal_pct", 10, 16),),
            high=(("heal_pct", 14, 22),),
            peak=(("heal_pct", 18, 28),),
            supreme=(("heal_pct", 24, 36),),
        ),
        description_builder=lambda rolls: f"造成伤害后，按本次实际伤害的 {rolls['heal_pct']}% 回复生命。",
    ),
    _define_power(
        "jueming",
        "绝命",
        roll_ranges_by_tier=_tier_rolls(
            low=(("execute_threshold_pct", 10, 14),),
            mid=(("execute_threshold_pct", 12, 16),),
            high=(("execute_threshold_pct", 14, 18),),
            peak=(("execute_threshold_pct", 16, 20),),
            supreme=(("execute_threshold_pct", 18, 24),),
        ),
        description_builder=lambda rolls: f"造成伤害后，若目标生命低于 {rolls['execute_threshold_pct']}%，则直接斩灭。",
    ),
    _define_power(
        "xuanjia",
        "玄甲",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 18, 24), ("reduce_pct", 50, 65)),
            mid=(("proc_pct", 22, 30), ("reduce_pct", 60, 75)),
            high=(("proc_pct", 28, 36), ("reduce_pct", 70, 85)),
            peak=(("proc_pct", 34, 42), ("reduce_pct", 80, 95)),
            supreme=(("proc_pct", 40, 50), ("reduce_pct", 100, 100)),
        ),
        description_builder=lambda rolls: f"每回合首次受击时，有 {rolls['proc_pct']}% 概率格挡本次伤害，减伤 {rolls['reduce_pct']}%。",
    ),
    _define_power(
        "fanji",
        "反棘",
        roll_ranges_by_tier=_tier_rolls(
            low=(("reflect_pct", 12, 18),),
            mid=(("reflect_pct", 16, 24),),
            high=(("reflect_pct", 22, 32),),
            peak=(("reflect_pct", 28, 40),),
            supreme=(("reflect_pct", 36, 50),),
        ),
        description_builder=lambda rolls: f"受击后，按本次实际承伤的 {rolls['reflect_pct']}% 反弹伤害。",
    ),
    _define_power(
        "guifeng",
        "归锋",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 12, 18), ("damage_pct", 55, 70)),
            mid=(("proc_pct", 16, 22), ("damage_pct", 65, 80)),
            high=(("proc_pct", 20, 28), ("damage_pct", 75, 90)),
            peak=(("proc_pct", 24, 34), ("damage_pct", 85, 100)),
            supreme=(("proc_pct", 30, 40), ("damage_pct", 100, 120)),
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
            low=(("proc_pct", 6, 9),),
            mid=(("proc_pct", 8, 11),),
            high=(("proc_pct", 10, 13),),
            peak=(("proc_pct", 12, 15),),
            supreme=(("proc_pct", 14, 18),),
        ),
        description_builder=lambda rolls: f"命中后有 {rolls['proc_pct']}% 概率封脉，使目标下次行动失效。",
    ),
    _define_power(
        "xuekuang",
        "血狂",
        roll_ranges_by_tier=_tier_rolls(
            low=(
                ("per_lost_10_pct", 4, 5),
                ("max_bonus_pct", 35, 35),
                ("frenzy_bonus_pct", 15, 22),
                ("frenzy_lifesteal_pct", 6, 10),
            ),
            mid=(
                ("per_lost_10_pct", 5, 6),
                ("max_bonus_pct", 50, 50),
                ("frenzy_bonus_pct", 20, 30),
                ("frenzy_lifesteal_pct", 8, 12),
            ),
            high=(
                ("per_lost_10_pct", 6, 7),
                ("max_bonus_pct", 70, 70),
                ("frenzy_bonus_pct", 28, 40),
                ("frenzy_lifesteal_pct", 10, 15),
            ),
            peak=(
                ("per_lost_10_pct", 7, 8),
                ("max_bonus_pct", 90, 90),
                ("frenzy_bonus_pct", 36, 52),
                ("frenzy_lifesteal_pct", 12, 18),
            ),
            supreme=(
                ("per_lost_10_pct", 8, 10),
                ("max_bonus_pct", 120, 120),
                ("frenzy_bonus_pct", 48, 70),
                ("frenzy_lifesteal_pct", 15, 22),
            ),
        ),
        description_builder=lambda rolls: (
            f"每损失 10% 最大生命，伤害提高 {rolls['per_lost_10_pct']}%，最高 {rolls['max_bonus_pct']}%；"
            f"生命低于 25% 时，额外增伤 {rolls['frenzy_bonus_pct']}%，并获得 {rolls['frenzy_lifesteal_pct']}% 吸血。"
        ),
    ),
)
SPIRIT_POWER_BY_ID = {definition.power_id: definition for definition in SPIRIT_POWER_DEFINITIONS}


def get_spirit_power_definition(power_id: str) -> SpiritPowerDefinition:
    return SPIRIT_POWER_BY_ID[power_id]
