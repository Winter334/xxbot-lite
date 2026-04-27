from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable, Mapping

RollMap = Mapping[str, int]


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
        return self.description_builder(self.normalize_rolls(rolls))

    def normalize_rolls(self, rolls: RollMap) -> dict[str, int]:
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
            low=(("heal_pct", 20, 28),),
            mid=(("heal_pct", 28, 38),),
            high=(("heal_pct", 38, 50),),
            peak=(("heal_pct", 50, 62),),
            supreme=(("heal_pct", 60, 75),),
        ),
        description_builder=lambda rolls: f"造成实际伤害后，按伤害的 {rolls['heal_pct']}% 回复生命；生息会进一步放大续航。",
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
        description_builder=lambda rolls: f"造成伤害后，若目标生命低于 {rolls['execute_threshold_pct']}%，则直接斩灭；目标负面层数会小幅抬高斩杀线。",
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
        description_builder=lambda rolls: f"受击后，按本次实际承伤的 {rolls['reflect_pct']}% 反弹伤害；守势减伤后反弹更高。",
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
        description_builder=lambda rolls: f"受击后有 {rolls['proc_pct']}% 概率立刻反击 1 次，本次反击造成 {rolls['damage_pct']}% 伤害；血线落后时更易触发。",
    ),
    _define_power(
        "niepan",
        "涅槃",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 25, 32), ("reduce_pct", 55, 65)),
            mid=(("heal_pct", 30, 38), ("reduce_pct", 65, 75)),
            high=(("heal_pct", 36, 46), ("reduce_pct", 75, 85)),
            peak=(("heal_pct", 44, 56), ("reduce_pct", 85, 94)),
            supreme=(("heal_pct", 52, 68), ("reduce_pct", 92, 100)),
        ),
        description_builder=lambda rolls: f"首次致死时不死，回复 {rolls['heal_pct']}% 最大生命，并获得 2 次 {rolls['reduce_pct']}% 守势。",
    ),
    _define_power(
        "jinmai",
        "禁脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("proc_pct", 12, 15),),
            mid=(("proc_pct", 15, 19),),
            high=(("proc_pct", 18, 24),),
            peak=(("proc_pct", 23, 29),),
            supreme=(("proc_pct", 28, 36),),
        ),
        description_builder=lambda rolls: f"命中后有 {rolls['proc_pct']}% 概率封脉，使目标下次行动失效；目标破步或创伤时概率提高。",
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
        description_builder=lambda rolls: f"每损失 10% 最大生命，伤害提高 {rolls['per_lost_10_pct']}%，最高 {rolls['max_bonus_pct']}%；低血时获得 {rolls['frenzy_lifesteal_pct']}% 吸血。",
    ),
    _define_power(
        "fenmai",
        "焚脉",
        roll_ranges_by_tier=_tier_rolls(
            low=(("ignite_pct", 50, 70),),
            mid=(("ignite_pct", 65, 90),),
            high=(("ignite_pct", 85, 115),),
            peak=(("ignite_pct", 110, 145),),
            supreme=(("ignite_pct", 140, 180),),
        ),
        description_builder=lambda rolls: f"命中带灼烧或灼痕目标后，追加本次伤害 {rolls['ignite_pct']}% 的神通伤害；灼痕越多越强。",
    ),
    _define_power(
        "luejie",
        "戮厄",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_debuff_pct", 12, 15), ("max_bonus_pct", 60, 60)),
            mid=(("per_debuff_pct", 15, 20), ("max_bonus_pct", 85, 85)),
            high=(("per_debuff_pct", 20, 24), ("max_bonus_pct", 110, 110)),
            peak=(("per_debuff_pct", 24, 28), ("max_bonus_pct", 135, 135)),
            supreme=(("per_debuff_pct", 28, 34), ("max_bonus_pct", 160, 160)),
        ),
        description_builder=lambda rolls: f"目标每有 1 个负面层数，本次伤害提高 {rolls['per_debuff_pct']}%，最高 {rolls['max_bonus_pct']}%。",
    ),
    _define_power(
        "chengshi",
        "乘势",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 25, 35),),
            mid=(("damage_pct", 35, 48),),
            high=(("damage_pct", 48, 62),),
            peak=(("damage_pct", 62, 78),),
            supreme=(("damage_pct", 78, 96),),
        ),
        description_builder=lambda rolls: f"若本次出手已受任意词条增伤，则额外增伤 {rolls['damage_pct']}%；消耗正面层数时收益更高。",
    ),
    _define_power(
        "lingyong",
        "灵涌",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_buff_pct", 6, 9), ("max_bonus_pct", 70, 70)),
            mid=(("per_buff_pct", 9, 12), ("max_bonus_pct", 95, 95)),
            high=(("per_buff_pct", 12, 15), ("max_bonus_pct", 120, 120)),
            peak=(("per_buff_pct", 15, 19), ("max_bonus_pct", 150, 150)),
            supreme=(("per_buff_pct", 19, 24), ("max_bonus_pct", 180, 180)),
        ),
        description_builder=lambda rolls: f"自身每有 1 个正面层数，伤害提高 {rolls['per_buff_pct']}%，最高 {rolls['max_bonus_pct']}%；灵势权重更高。",
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
            low=(("atk_pct", 10, 14), ("agi_pct", 10, 14)),
            mid=(("atk_pct", 14, 18), ("agi_pct", 14, 18)),
            high=(("atk_pct", 18, 23), ("agi_pct", 18, 23)),
            peak=(("atk_pct", 23, 28), ("agi_pct", 23, 28)),
            supreme=(("atk_pct", 28, 34), ("agi_pct", 28, 34)),
        ),
        description_builder=lambda rolls: f"命中带负面层数目标后，偷取杀伐、身法各 {rolls['atk_pct']}% / {rolls['agi_pct']}%，整场生效但有上限。",
    ),
    _define_power(
        "zhenling",
        "镇灵",
        roll_ranges_by_tier=_tier_rolls(
            low=(("heal_pct", 5, 7),),
            mid=(("heal_pct", 7, 10),),
            high=(("heal_pct", 10, 13),),
            peak=(("heal_pct", 13, 16),),
            supreme=(("heal_pct", 16, 20),),
        ),
        description_builder=lambda rolls: f"每回合首次受创时，若自身带负面层数，则净去 1 层并回复 {rolls['heal_pct']}% 最大生命。",
    ),
    _define_power(
        "chunsheng",
        "春生",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 35, 50),),
            mid=(("damage_pct", 50, 68),),
            high=(("damage_pct", 68, 86),),
            peak=(("damage_pct", 86, 108),),
            supreme=(("damage_pct", 108, 130),),
        ),
        description_builder=lambda rolls: f"每次受到治疗后，下一次造成伤害提高 {rolls['damage_pct']}%；治疗溢出时额外凝成守势。",
    ),
    _define_power(
        "suijue",
        "碎阙",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 45, 62), ("proc_pct", 30, 40)),
            mid=(("damage_pct", 62, 80), ("proc_pct", 38, 50)),
            high=(("damage_pct", 80, 100), ("proc_pct", 48, 62)),
            peak=(("damage_pct", 100, 124), ("proc_pct", 60, 74)),
            supreme=(("damage_pct", 124, 150), ("proc_pct", 72, 88)),
        ),
        description_builder=lambda rolls: f"命中带守势或多个正面层数目标时，追加 {rolls['damage_pct']}% 伤害，并有 {rolls['proc_pct']}% 概率打散 1 层正面状态。",
    ),
    _define_power(
        "mingche",
        "明澈",
        roll_ranges_by_tier=_tier_rolls(
            low=(("per_stack_pct", 10, 13),),
            mid=(("per_stack_pct", 13, 16),),
            high=(("per_stack_pct", 16, 20),),
            peak=(("per_stack_pct", 20, 25),),
            supreme=(("per_stack_pct", 25, 30),),
        ),
        description_builder=lambda rolls: f"自身净化负面层数后获得明澈；每层提高伤害和减伤 {rolls['per_stack_pct']}%，最多 4 层。",
    ),
    _define_power(
        "zhuifeng",
        "追风",
        roll_ranges_by_tier=_tier_rolls(
            low=(("damage_pct", 35, 48),),
            mid=(("damage_pct", 48, 62),),
            high=(("damage_pct", 62, 78),),
            peak=(("damage_pct", 78, 96),),
            supreme=(("damage_pct", 96, 115),),
        ),
        description_builder=lambda rolls: f"若本回合先手或身法高出目标 50%，命中后追加 {rolls['damage_pct']}% 神通伤害；第 4 回合后逐步衰减。",
    ),
)
SPIRIT_POWER_BY_ID = {definition.power_id: definition for definition in SPIRIT_POWER_DEFINITIONS}


def get_spirit_power_definition(power_id: str) -> SpiritPowerDefinition:
    return SPIRIT_POWER_BY_ID[power_id]
