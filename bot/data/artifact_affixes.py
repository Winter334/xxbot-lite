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
        ("atk_pct", 4, 9),
        description_builder=lambda rolls: (
            f"造成伤害后必定凝成 1 层灵势；每层灵势提高 {rolls['atk_pct']}% 杀伐，最多 10 层"
        ),
    ),
    _define(
        "lueying",
        "掠影",
        "battle_start",
        ("agi_pct", 30, 70),
        ("drain_pct", 3, 7),
        description_builder=lambda rolls: (
            f"整场身法提高 {rolls['agi_pct']}%；命中时必定汲取目标 {rolls['drain_pct']}% 身法转移至自身（整场生效，有上限）。"
        ),
    ),
    _define(
        "zhoufu",
        "咒缚",
        "round_start",
        ("reduce_down_pct", 3, 10),
        ("max_stacks", 7, 7),
        description_builder=lambda rolls: (
            f"每回合给敌方附加 1 层「咒缚」（每层降减伤 {rolls['reduce_down_pct']}%，最多 {rolls['max_stacks']} 层）+ 1 层绝命印记；"
            f"咒缚被净化时每层额外叠加 1 层绝命印记。"
        ),
    ),
    _define(
        "juling",
        "聚灵",
        "round_start",
        ("atk_pct", 4, 9),
        ("late_damage_pct", 10, 18),
        description_builder=lambda rolls: (
            f"每回合开始获得 1 层灵势，最多 10 层；每层杀伐提高 {rolls['atk_pct']}%，"
            f"并额外提高 {rolls['late_damage_pct']}% 造成伤害"
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
        ("burn_stacks", 2, 4),
        ("burn_atk_pct", 25, 55),
        description_builder=lambda rolls: (
            f"命中必定附加 {rolls['burn_stacks']} 层灼烧；灼烧每层每回合造成杀伐 {rolls['burn_atk_pct']}% 伤害，"
            f"每回合衰减 1 层（多次叠加时单层伤害取较高值）"
        ),
    ),
    _define(
        "zhenpo",
        "震魄",
        "on_hit",
        ("proc_pct", 30, 60),
        ("agi_down_pct", 8, 16),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率附加 1 层破步，每层使目标身法降低 {rolls['agi_down_pct']}%，"
            f"最多 4 层；已满 4 层时改为震慑目标（跳过下次行动）"
        ),
    ),
    _define(
        "manzhou",
        "蔓咒",
        "on_hit",
        ("atk_down_pct", 4, 12),
        ("max_stacks", 7, 7),
        description_builder=lambda rolls: (
            f"命中附加 1 层「蔓咒」（每层降杀伐 {rolls['atk_down_pct']}%，最多 {rolls['max_stacks']} 层）+ 1 层绝命印记；"
            f"目标有蔓咒时回合结束自动叠加 1 层。"
        ),
    ),
    _define(
        "huichun",
        "回春",
        "on_low_hp",
        ("heal_pct", 30, 100),
        ("shengxi_stacks", 2, 5),
        description_builder=lambda rolls: (
            f"生命首次低于 50% 与 25% 时各触发一次：回复 {rolls['heal_pct']}% 最大生命并叠加 {rolls['shengxi_stacks']} 层生息。"
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
        "liekai",
        "裂铠",
        "on_low_hp",
        ("shield_pct", 22, 42),
        ("backlash_pct", 35, 60),
        description_builder=lambda rolls: (
            f"生命低于 30% 时获得「裂铠」护盾（{rolls['shield_pct']}% 最大生命）；"
            f"护盾存在时受击给攻击者附加 1 层绝命印记；"
            f"护盾破碎时对击碎者造成吸收量 {rolls['backlash_pct']}% 伤害 + 2 层绝命印记。"
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
        ("stacks", 1, 3),
        ("buff_pct", 5, 15),
        description_builder=lambda rolls: (
            f"命中必定净化敌方 {rolls['stacks']} 层正面效果；每净化 1 层自身获得 {rolls['buff_pct']}% 伤害加成（持续 2 回合）"
        ),
    ),
    _define(
        "jinghua",
        "净华",
        "round_start",
        ("stacks", 1, 4),
        description_builder=lambda rolls: (
            f"每回合开始必定净化自身 {rolls['stacks']} 层负面效果。"
        ),
    ),
    _define(
        "guiyuan",
        "归元",
        "battle_start",
        ("max_hp_pct", 8, 42),
        description_builder=lambda rolls: (
            f"战斗开始时永久提高 {rolls['max_hp_pct']}% 最大生命（同步治疗等量生命）；多件可叠加。"
        ),
    ),
    _define(
        "cangbi",
        "藏壁",
        "on_be_hit",
        ("reduce_pct", 25, 50),
        description_builder=lambda rolls: (
            f"每回合首次受击时，获得 1 层守势（本层抵消 {rolls['reduce_pct']}% 受击伤害）。"
        ),
    ),
    _define(
        "jifeng",
        "疾锋",
        "on_hit",
        ("agi_pct", 15, 30),
        ("damage_pct", 10, 22),
        description_builder=lambda rolls: (
            f"命中后获得 1 层疾锋（最多 3 层）；每层提高 {rolls['agi_pct']}% 身法与 {rolls['damage_pct']}% 伤害。受击时失去 1 层。不限回合。"
        ),
    ),
    # ── 灼烧流 ──
    _define(
        "fenxin",
        "焚心",
        "on_burn_apply",
        ("atk_down_pct", 6, 12),
        ("agi_down_pct", 6, 12),
        ("max_stacks", 6, 8),
        description_builder=lambda rolls: (
            f"每次给目标附加灼烧时，目标获得 1 层焚心，最多 {rolls['max_stacks']} 层；"
            f"每层使目标杀伐降低 {rolls['atk_down_pct']}%、身法降低 {rolls['agi_down_pct']}%"
        ),
    ),
    _define(
        "jinhuo",
        "烬火",
        "on_hit",
        ("proc_pct", 50, 90),
        ("burn_stacks_gain", 1, 2),
        description_builder=lambda rolls: (
            f"攻击灼烧目标时，{rolls['proc_pct']}% 概率消耗目标 1 层正面状态，"
            f"将其转化为 {rolls['burn_stacks_gain']} 层灼烧"
        ),
    ),
    _define(
        "fenjie",
        "焚劫",
        "on_burn_apply",
        ("vuln_pct", 6, 12),
        ("heal_down_pct", 8, 15),
        ("max_stacks", 6, 8),
        description_builder=lambda rolls: (
            f"每次给目标附加灼烧时，目标获得 1 层焚劫，最多 {rolls['max_stacks']} 层；"
            f"每层使目标承伤提高 {rolls['vuln_pct']}%、受疗降低 {rolls['heal_down_pct']}%"
        ),
    ),
    _define(
        "yujin",
        "余烬",
        "on_burn_consumed",
        ("proc_pct", 35, 70),
        ("relight_stacks", 2, 4),
        ("relight_burn_pct", 20, 40),
        description_builder=lambda rolls: (
            f"目标灼烧被消耗（自然烧尽或被引爆）时，{rolls['proc_pct']}% 概率重新点燃 "
            f"{rolls['relight_stacks']} 层灼烧（每层 {rolls['relight_burn_pct']}% 杀伐）"
        ),
    ),
    # ── 身法流 ──
    _define(
        "fengxing",
        "风行",
        "on_dodge",
        ("damage_pct", 12, 28),
        ("heal_pct", 2, 6),
        description_builder=lambda rolls: (
            f"闪避后获得 1 层风行（最多 5 层）；攻击时消耗全部层数，每层提高本次伤害 {rolls['damage_pct']}% 并回复 {rolls['heal_pct']}% 最大生命。"
        ),
    ),
    _define(
        "huanbu",
        "幻步",
        "on_dodge",
        ("dodge_pct", 3, 7),
        ("counter_pct", 25, 55),
        description_builder=lambda rolls: (
            f"闪避后获得 1 层幻步（最多 3 层）；每层提供 {rolls['dodge_pct']}% 闪避；"
            f"满 3 层时闪避将触发反击，造成 {rolls['counter_pct']}% 杀伐伤害。暴击后消散 1 层。"
        ),
    ),
    _define(
        "pokong",
        "破空",
        "on_crit",
        ("damage_ratio_pct", 55, 80),
        description_builder=lambda rolls: (
            f"暴击后必定发动一次追击，造成 {rolls['damage_ratio_pct']}% 杀伐伤害（不享受增伤加成）。"
        ),
    ),
    # ── 暴击流 ──
    _define(
        "tianwei",
        "天威",
        "on_crit",
        ("crit_pct", 4, 7),
        ("crit_damage_pct", 8, 13),
        description_builder=lambda rolls: (
            f"暴击后获得 1 层天威，最多 6 层；每层提高 {rolls['crit_pct']}% 暴击率与 {rolls['crit_damage_pct']}% 暴击伤害"
        ),
    ),
    _define(
        "leiyin",
        "雷引",
        "on_crit",
        ("next_damage_pct", 18, 28),
        ("burst_pct", 4, 7),
        description_builder=lambda rolls: (
            f"暴击后蓄势：下一次出手伤害提高 {rolls['next_damage_pct']}%；每累计 3 次暴击触发一次小型雷劫，"
            f"造成 {rolls['burst_pct']}% 最大生命的真伤（豁免韧性）"
        ),
    ),
    _define(
        "liekong",
        "裂空",
        "before_attack",
        ("pierce_pct", 15, 25),
        description_builder=lambda rolls: (
            f"对身上有雷殛标记的目标，每层雷殛额外无视 {rolls['pierce_pct']}% 减伤"
        ),
    ),
    # ── 净化流 ──
    _define(
        "qingxin",
        "清心",
        "round_start",
        ("stacks", 1, 2),
        ("heal_pct", 1, 10),
        description_builder=lambda rolls: (
            f"每回合开始必定净化自身 {rolls['stacks']} 层负面效果；"
            f"若成功净化，回复 {rolls['heal_pct']}% 最大生命"
        ),
    ),
    _define(
        "zhuanji",
        "转机",
        "on_cleanse",
        ("damage_pct", 15, 35),
        ("max_layers", 2, 6),
        description_builder=lambda rolls: (
            f"任意净化效果触发时，每净化 1 层追加 {rolls['damage_pct']}% 杀伐伤害（单次净化最多计算 {rolls['max_layers']} 层）"
        ),
    ),
    # ── 通用中立 ──
    _define(
        "guben",
        "固本",
        "battle_start",
        ("shield_pct", 25, 50),
        description_builder=lambda rolls: (
            f"战斗开始获得 {rolls['shield_pct']}% 最大生命的护盾（护盾免疫净化）。"
        ),
    ),
    _define(
        "shiyin",
        "噬印",
        "on_hit",
        ("proc_pct", 40, 70),
        ("drain_per_mark_pct", 2, 4),
        description_builder=lambda rolls: (
            f"命中后有 {rolls['proc_pct']}% 概率吸取目标「绝命印记层数×{rolls['drain_per_mark_pct']}%」最大生命；"
            f"若目标无绝命印记则附加 1 层。"
        ),
    ),
    _define(
        "xianji",
        "先机",
        "battle_start",
        ("agi_pct", 20, 50),
        ("dodge_bonus_pct", 8, 18),
        description_builder=lambda rolls: (
            f"整场身法提高 {rolls['agi_pct']}%（影响先手判定）；前 2 回合额外获得 {rolls['dodge_bonus_pct']}% 闪避率。"
        ),
    ),
    _define(
        "huyuan",
        "护元",
        "battle_start",
        ("start_stacks", 1, 4),
        ("per_battle_cap", 3, 7),
        description_builder=lambda rolls: (
            f"战斗开始时叠加 {rolls['start_stacks']} 层生息；自身受到治疗时额外叠加 1 层生息，"
            f"本词条单局最多叠加 {rolls['per_battle_cap']} 层（多件分别独立计数）。"
        ),
    ),
    _define(
        "yangyuan",
        "养元",
        "round_start",
        ("heal_pct", 1, 10),
        description_builder=lambda rolls: (
            f"每回合开始时回复 {rolls['heal_pct']}% 最大生命（多件可叠加）。"
        ),
    ),
    _define(
        "xuming",
        "续命",
        "round_start",
        ("cost_stacks", 1, 4),
        ("heal_per_stack", 2, 15),
        description_builder=lambda rolls: (
            f"每回合开始时若生息层数大于 {rolls['cost_stacks']}，消耗 {rolls['cost_stacks']} 层生息回复 "
            f"{rolls['cost_stacks']} × {rolls['heal_per_stack']}% 最大生命。"
        ),
    ),
    _define(
        "huisheng",
        "秽生",
        "round_start",
        ("stacks", 1, 3),
        ("ally_pct", 35, 80),
        description_builder=lambda rolls: (
            f"每回合附加 {rolls['stacks']} 层随机负面效果到随机目标；有 {rolls['ally_pct']}% 概率选择敌方，否则选择自身。"
        ),
    ),
    _define(
        "lingyi",
        "灵溢",
        "round_start",
        ("stacks", 1, 3),
        ("ally_pct", 35, 80),
        description_builder=lambda rolls: (
            f"每回合附加 {rolls['stacks']} 层随机增益效果到随机目标；有 {rolls['ally_pct']}% 概率选择自身，否则选择敌方。"
        ),
    ),
    _define(
        "fanshi",
        "反噬",
        "on_effect_lost",
        ("damage_pct", 15, 35),
        description_builder=lambda rolls: (
            f"敌方每失去 1 个正面效果（过期/被净化/被打散），对其造成 {rolls['damage_pct']}% 杀伐伤害。"
        ),
    ),
    _define(
        "tanshi",
        "贪噬",
        "on_gain_positive",
        ("per_stack_pct", 3, 8),
        ("max_stacks", 5, 10),
        description_builder=lambda rolls: (
            f"自身每获得 1 个正面效果叠 1 层贪噬；每层提高 {rolls['per_stack_pct']}% 造成伤害，最多 {rolls['max_stacks']} 层。（贪噬自身的层数不计入触发）"
        ),
    ),
    # ── 反爆发 ──
    _define(
        "chenchen",
        "承尘",
        "on_be_hit",
        ("threshold_pct", 22, 32),
        ("reduction_pct", 30, 55),
        description_builder=lambda rolls: (
            f"单次受到的伤害若超过自身最大生命 {rolls['threshold_pct']}%，"
            f"溢出部分削减 {rolls['reduction_pct']}%（多件取最低阈值与最高削减）。"
        ),
    ),
    _define(
        "tongming",
        "透命",
        "before_attack",
        ("pierce_pct", 6, 15),
        description_builder=lambda rolls: (
            f"每次攻击额外无视目标 {rolls['pierce_pct']}% 减伤（多件叠加，与登霄/裂空共享通道）。"
        ),
    ),
)

ARTIFACT_AFFIXES_BY_ID = {definition.affix_id: definition for definition in ARTIFACT_AFFIX_DEFINITIONS}


def get_artifact_affix_definition(affix_id: str) -> ArtifactAffixDefinition:
    return ARTIFACT_AFFIXES_BY_ID[affix_id]
