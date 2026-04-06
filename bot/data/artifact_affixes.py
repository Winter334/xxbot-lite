from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable, Mapping


RollMap = Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ArtifactAffixEntry:
    slot: int
    affix_id: str
    rolls: dict[str, int]

    def to_payload(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "affix_id": self.affix_id,
            "rolls": dict(self.rolls),
        }


@dataclass(frozen=True, slots=True)
class ArtifactAffixDefinition:
    affix_id: str
    name: str
    trigger: str
    scene_tags: tuple[str, ...]
    roll_ranges: tuple[tuple[str, int, int], ...]
    description_builder: Callable[[RollMap], str]

    def roll(self, rng: random.Random) -> dict[str, int]:
        return {key: rng.randint(low, high) for key, low, high in self.roll_ranges}

    def describe(self, rolls: RollMap) -> str:
        return self.description_builder(rolls)

    def matches_scene(self, scene_tags: set[str]) -> bool:
        return all(tag in scene_tags for tag in self.scene_tags)


def _define(
    affix_id: str,
    name: str,
    trigger: str,
    *roll_ranges: tuple[str, int, int],
    scene_tags: tuple[str, ...] = (),
    description_builder: Callable[[RollMap], str],
) -> ArtifactAffixDefinition:
    return ArtifactAffixDefinition(
        affix_id=affix_id,
        name=name,
        trigger=trigger,
        scene_tags=scene_tags,
        roll_ranges=roll_ranges,
        description_builder=description_builder,
    )


ARTIFACT_AFFIX_DEFINITIONS = (
    _define(
        "ningshen",
        "凝神",
        "battle_start",
        ("atk_pct", 4, 24),
        description_builder=lambda rolls: f"战斗开始时获得 2 回合凝神；杀伐提高 {rolls['atk_pct']}%",
    ),
    _define(
        "lueying",
        "掠影",
        "battle_start",
        ("agi_pct", 4, 24),
        description_builder=lambda rolls: f"战斗开始时获得 2 回合掠影；身法提高 {rolls['agi_pct']}%",
    ),
    _define(
        "zhenmai",
        "镇脉",
        "battle_start",
        ("reduce_pct", 6, 26),
        description_builder=lambda rolls: f"战斗开始时获得镇脉；前 2 次受击减伤 {rolls['reduce_pct']}%",
    ),
    _define(
        "juling",
        "聚灵",
        "round_start",
        ("atk_pct", 2, 8),
        description_builder=lambda rolls: f"前 3 回合开始时叠加聚灵；每层杀伐提高 {rolls['atk_pct']}%",
    ),
    _define(
        "shigu",
        "蚀骨",
        "on_hit",
        ("proc_pct", 12, 45),
        ("vuln_pct", 4, 18),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 2 回合易伤；目标受到伤害提高 {rolls['vuln_pct']}%"
        ),
    ),
    _define(
        "zhuohun",
        "灼魂",
        "on_hit",
        ("proc_pct", 10, 35),
        ("burn_pct", 1, 5),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 2 回合灼烧；每回合造成目标最大生命 {rolls['burn_pct']}% 伤害"
        ),
    ),
    _define(
        "zhenpo",
        "震魄",
        "on_hit",
        ("proc_pct", 12, 40),
        ("agi_down_pct", 4, 18),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 2 回合震魄；目标身法降低 {rolls['agi_down_pct']}%"
        ),
    ),
    _define(
        "fengfeng",
        "封锋",
        "on_hit",
        ("proc_pct", 12, 40),
        ("atk_down_pct", 4, 18),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 2 回合封锋；目标杀伐降低 {rolls['atk_down_pct']}%"
        ),
    ),
    _define(
        "huichun",
        "回春",
        "on_low_hp",
        ("heal_pct", 18, 42),
        description_builder=lambda rolls: f"生命首次低于 50% 时，回复 {rolls['heal_pct']}% 最大生命",
    ),
    _define(
        "zhuiming",
        "追命",
        "before_attack",
        ("damage_pct", 6, 26),
        description_builder=lambda rolls: f"若目标当前生命高于 70%，本次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "duanyue",
        "断岳",
        "before_attack",
        ("damage_pct", 6, 24),
        description_builder=lambda rolls: f"若目标带有减益状态，本次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "kuangfeng",
        "狂锋",
        "on_crit",
        ("damage_pct", 8, 30),
        description_builder=lambda rolls: f"暴击后获得狂锋；下一次出手伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "dengxiao",
        "登霄",
        "battle_start",
        ("atk_pct", 6, 26),
        ("agi_pct", 3, 14),
        scene_tags=("scene_tower",),
        description_builder=lambda rolls: (
            f"通天塔战斗开始时获得 2 回合登霄；杀伐提高 {rolls['atk_pct']}%，身法提高 {rolls['agi_pct']}%"
        ),
    ),
    _define(
        "zhenguan",
        "镇关",
        "before_attack",
        ("damage_pct", 10, 32),
        scene_tags=("scene_boss",),
        description_builder=lambda rolls: f"对守关 / BOSS 目标时，本次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "buqu",
        "不屈",
        "on_low_hp",
        ("heal_pct", 15, 35),
        ("reduce_pct", 8, 24),
        scene_tags=("scene_tower",),
        description_builder=lambda rolls: (
            f"通天塔中首次生命低于 40% 时，回复 {rolls['heal_pct']}% 最大生命；下一次受击减伤 {rolls['reduce_pct']}%"
        ),
    ),
    _define(
        "zhengheng",
        "争衡",
        "battle_start",
        ("agi_pct", 8, 28),
        scene_tags=("scene_ladder",),
        description_builder=lambda rolls: f"论道开始时获得 2 回合争衡；身法提高 {rolls['agi_pct']}%",
    ),
    _define(
        "yazhen",
        "压阵",
        "on_be_hit",
        ("damage_pct", 8, 26),
        scene_tags=("scene_ladder",),
        description_builder=lambda rolls: f"论道中前 2 次受击后触发；下一次出手伤害提高 {rolls['damage_pct']}%",
    ),
)

ARTIFACT_AFFIXES_BY_ID = {definition.affix_id: definition for definition in ARTIFACT_AFFIX_DEFINITIONS}


def get_artifact_affix_definition(affix_id: str) -> ArtifactAffixDefinition:
    return ARTIFACT_AFFIXES_BY_ID[affix_id]
