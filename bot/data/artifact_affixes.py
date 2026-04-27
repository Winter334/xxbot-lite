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
        return self.description_builder(self.normalize_rolls(rolls))

    def normalize_rolls(self, rolls: RollMap) -> dict[str, int]:
        normalized = dict(rolls)
        for key, low, _high in self.roll_ranges:
            normalized.setdefault(key, low)
        return normalized

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
        "on_hit",
        ("proc_pct", 35, 70),
        ("atk_pct", 3, 7),
        description_builder=lambda rolls: (
            f"造成伤害后有 {rolls['proc_pct']}% 概率凝成 1 层灵势；每层灵势提高 {rolls['atk_pct']}% 杀伐，最多 8 层"
        ),
    ),
    _define(
        "lueying",
        "掠影",
        "battle_start",
        ("agi_pct", 35, 80),
        ("proc_pct", 30, 60),
        ("agi_down_pct", 8, 16),
        description_builder=lambda rolls: (
            f"整场身法提高 {rolls['agi_pct']}%；命中后有 {rolls['proc_pct']}% 概率附加 1 层破步，"
            f"每层使目标身法降低 {rolls['agi_down_pct']}%，最多 4 层"
        ),
    ),
    _define(
        "zhenmai",
        "镇脉",
        "battle_start",
        ("reduce_pct", 25, 55),
        description_builder=lambda rolls: f"战斗开始获得 3 层守势；每层抵消一次受击的 {rolls['reduce_pct']}% 伤害",
    ),
    _define(
        "juling",
        "聚灵",
        "round_start",
        ("atk_pct", 3, 7),
        ("late_damage_pct", 8, 15),
        description_builder=lambda rolls: (
            f"每回合开始获得 1 层灵势，最多 8 层；每层杀伐提高 {rolls['atk_pct']}%，"
            f"6 层后额外提高 {rolls['late_damage_pct']}% 造成伤害"
        ),
    ),
    _define(
        "shigu",
        "蚀骨",
        "on_hit",
        ("proc_pct", 30, 60),
        ("heal_down_pct", 4, 8),
        ("vuln_pct", 2, 5),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 1 层创伤；每层降低受疗 {rolls['heal_down_pct']}%，"
            f"并使承伤提高 {rolls['vuln_pct']}%，最多 5 层"
        ),
    ),
    _define(
        "zhuohun",
        "灼魂",
        "on_hit",
        ("proc_pct", 25, 50),
        ("burn_pct", 2, 6),
        ("scar_bonus_pct", 4, 8),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 2 回合灼烧并叠 1 层灼痕；灼烧造成最大生命 {rolls['burn_pct']}% 伤害，"
            f"每层灼痕使灼烧伤害提高 {rolls['scar_bonus_pct']}%，最多 6 层"
        ),
    ),
    _define(
        "zhenpo",
        "震魄",
        "on_hit",
        ("proc_pct", 30, 60),
        ("agi_down_pct", 8, 16),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 1 层破步；每层使目标身法降低 {rolls['agi_down_pct']}%，最多 4 层"
        ),
    ),
    _define(
        "fengfeng",
        "封锋",
        "on_hit",
        ("proc_pct", 25, 50),
        ("atk_down_pct", 6, 12),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 1 层断锋；每层使目标杀伐降低 {rolls['atk_down_pct']}%，最多 4 层"
        ),
    ),
    _define(
        "huichun",
        "回春",
        "on_low_hp",
        ("heal_pct", 25, 55),
        ("heal_bonus_pct", 8, 16),
        description_builder=lambda rolls: (
            f"生命首次低于 50% 时回复 {rolls['heal_pct']}% 最大生命，并获得 2 层生息；"
            f"每层生息提高受疗 {rolls['heal_bonus_pct']}%"
        ),
    ),
    _define(
        "zhuiming",
        "追命",
        "before_attack",
        ("damage_pct", 35, 75),
        description_builder=lambda rolls: f"攻击生命高于 70% 的目标时，本次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "duanyue",
        "断岳",
        "before_attack",
        ("per_debuff_pct", 10, 20),
        ("max_bonus_pct", 50, 100),
        description_builder=lambda rolls: (
            f"目标每有 1 个负面层数，本次伤害提高 {rolls['per_debuff_pct']}%，最高 {rolls['max_bonus_pct']}%"
        ),
    ),
    _define(
        "kuangfeng",
        "狂锋",
        "on_crit",
        ("damage_pct", 70, 150),
        description_builder=lambda rolls: f"暴击后获得一次狂锋；下一次造成伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "dengxiao",
        "登霄",
        "round_end",
        ("damage_pct", 4, 9),
        ("pierce_pct", 3, 7),
        description_builder=lambda rolls: (
            f"每回合结束获得 1 层登霄，最多 8 层；每层提高 {rolls['damage_pct']}% 伤害，"
            f"6 层后额外获得 {rolls['pierce_pct']}% 减伤穿透"
        ),
    ),
    _define(
        "zhenguan",
        "镇关",
        "before_attack",
        ("damage_pct", 45, 110),
        description_builder=lambda rolls: f"攻击带守势、高减伤或生命比例高于自身的目标时，本次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "buqu",
        "不屈",
        "on_low_hp",
        ("heal_pct", 15, 35),
        ("reduce_pct", 35, 70),
        ("damage_pct", 60, 130),
        description_builder=lambda rolls: (
            f"首次生命低于 35% 时回复 {rolls['heal_pct']}%，获得 2 层 {rolls['reduce_pct']}% 守势，"
            f"并使下一次伤害提高 {rolls['damage_pct']}%"
        ),
    ),
    _define(
        "zhengheng",
        "争衡",
        "before_attack",
        ("damage_pct", 20, 60),
        description_builder=lambda rolls: f"自身生命比例低于目标时，本次伤害提高至多 {rolls['damage_pct']}%，差距越大收益越高",
    ),
    _define(
        "yazhen",
        "压阵",
        "on_be_hit",
        ("damage_pct", 35, 80),
        description_builder=lambda rolls: f"前 3 次受击后获得 1 层压阵；每层使下一次伤害提高 {rolls['damage_pct']}%",
    ),
    _define(
        "ranjin",
        "燃烬",
        "round_end",
        ("damage_pct", 2, 5),
        description_builder=lambda rolls: f"回合结束时，目标每 2 层灼痕追加最大生命 {rolls['damage_pct']}% 的燃烬伤害；6 层后翻倍",
    ),
    _define(
        "liechuang",
        "裂创",
        "on_hit",
        ("damage_pct", 20, 55),
        ("heal_down_pct", 5, 10),
        description_builder=lambda rolls: (
            f"命中生命低于 60% 或已有创伤的目标时附加 1 层创伤；目标已有创伤时本次后续伤害提高 {rolls['damage_pct']}%"
        ),
    ),
    _define(
        "suoling",
        "锁灵",
        "on_hit",
        ("proc_pct", 35, 70),
        ("damage_pct", 25, 65),
        description_builder=lambda rolls: (
            f"命中带正面层数的目标时，有 {rolls['proc_pct']}% 概率打散 1 层，并追加本次伤害 {rolls['damage_pct']}%"
        ),
    ),
    _define(
        "jinghua",
        "净华",
        "round_start",
        ("proc_pct", 35, 70),
        ("reduce_pct", 20, 45),
        description_builder=lambda rolls: (
            f"回合开始若自身有负面层数，有 {rolls['proc_pct']}% 概率净化 1 层，并获得 1 层 {rolls['reduce_pct']}% 守势"
        ),
    ),
    _define(
        "guiyuan",
        "归元",
        "on_heal",
        ("damage_pct", 30, 75),
        ("heal_bonus_pct", 6, 12),
        description_builder=lambda rolls: (
            f"每次受到治疗后获得 1 层生息；出手时消耗 1 层使本次伤害提高 {rolls['damage_pct']}%，"
            f"每层生息提高受疗 {rolls['heal_bonus_pct']}%"
        ),
    ),
    _define(
        "cangbi",
        "藏壁",
        "on_be_hit",
        ("reduce_pct", 35, 75),
        description_builder=lambda rolls: f"每回合首次受击降低 {rolls['reduce_pct']}% 伤害，并获得 1 层守势",
    ),
    _define(
        "jifeng",
        "疾锋",
        "on_hit",
        ("agi_pct", 20, 40),
        ("damage_pct", 15, 30),
        description_builder=lambda rolls: (
            f"前 3 回合命中后获得 1 层疾锋，最多 3 层；每层提高 {rolls['agi_pct']}% 身法与 {rolls['damage_pct']}% 伤害"
        ),
    ),
)

ARTIFACT_AFFIXES_BY_ID = {definition.affix_id: definition for definition in ARTIFACT_AFFIX_DEFINITIONS}


def get_artifact_affix_definition(affix_id: str) -> ArtifactAffixDefinition:
    return ARTIFACT_AFFIXES_BY_ID[affix_id]
