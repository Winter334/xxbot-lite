from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RealmStage:
    realm_key: str
    realm_index: int
    realm_name: str
    stage_key: str
    stage_index: int
    stage_name: str
    display_name: str
    global_stage_index: int
    cultivation_max: int
    base_atk: int
    base_def: int
    base_agi: int
    reinforce_cap: int


_ROWS = [
    ("lianqi", 1, "炼气", "early", 1, "前期", "炼气·前期", 1, 100, 10, 8, 6, 10),
    ("lianqi", 1, "炼气", "mid", 2, "中期", "炼气·中期", 2, 180, 18, 14, 11, 10),
    ("lianqi", 1, "炼气", "late", 3, "后期", "炼气·后期", 3, 320, 32, 25, 20, 10),
    ("lianqi", 1, "炼气", "perfect", 4, "圆满", "炼气·圆满", 4, 560, 56, 45, 36, 10),
    ("zhuji", 2, "筑基", "early", 1, "前期", "筑基·前期", 5, 800, 80, 65, 50, 20),
    ("zhuji", 2, "筑基", "mid", 2, "中期", "筑基·中期", 6, 1400, 140, 110, 88, 20),
    ("zhuji", 2, "筑基", "late", 3, "后期", "筑基·后期", 7, 2400, 240, 200, 150, 20),
    ("zhuji", 2, "筑基", "perfect", 4, "圆满", "筑基·圆满", 8, 4200, 420, 340, 270, 20),
    ("jiedan", 3, "结丹", "early", 1, "前期", "结丹·前期", 9, 9000, 900, 720, 580, 30),
    ("jiedan", 3, "结丹", "mid", 2, "中期", "结丹·中期", 10, 15000, 1500, 1200, 970, 30),
    ("jiedan", 3, "结丹", "late", 3, "后期", "结丹·后期", 11, 25000, 2500, 2000, 1600, 30),
    ("jiedan", 3, "结丹", "perfect", 4, "圆满", "结丹·圆满", 12, 42000, 4200, 3400, 2700, 30),
    ("yuanying", 4, "元婴", "early", 1, "前期", "元婴·前期", 13, 120000, 8000, 6500, 5200, 40),
    ("yuanying", 4, "元婴", "mid", 2, "中期", "元婴·中期", 14, 200000, 11000, 8800, 7100, 40),
    ("yuanying", 4, "元婴", "late", 3, "后期", "元婴·后期", 15, 330000, 15000, 12000, 9600, 40),
    ("yuanying", 4, "元婴", "perfect", 4, "圆满", "元婴·圆满", 16, 550000, 20000, 16000, 13000, 40),
    ("huashen", 5, "化神", "early", 1, "前期", "化神·前期", 17, 900000, 30000, 24000, 19000, 50),
    ("huashen", 5, "化神", "mid", 2, "中期", "化神·中期", 18, 1420000, 40000, 32000, 25000, 50),
    ("huashen", 5, "化神", "late", 3, "后期", "化神·后期", 19, 2230000, 53000, 42000, 34000, 50),
    ("huashen", 5, "化神", "perfect", 4, "圆满", "化神·圆满", 20, 3500000, 70000, 56000, 45000, 50),
    ("lianxu", 6, "炼虚", "early", 1, "前期", "炼虚·前期", 21, 8000000, 120000, 96000, 76000, 60),
    ("lianxu", 6, "炼虚", "mid", 2, "中期", "炼虚·中期", 22, 12430000, 160000, 130000, 100000, 60),
    ("lianxu", 6, "炼虚", "late", 3, "后期", "炼虚·后期", 23, 19310000, 210000, 170000, 135000, 60),
    ("lianxu", 6, "炼虚", "perfect", 4, "圆满", "炼虚·圆满", 24, 30000000, 280000, 225000, 180000, 60),
    ("heti", 7, "合体", "early", 1, "前期", "合体·前期", 25, 70000000, 450000, 360000, 290000, 70),
    ("heti", 7, "合体", "mid", 2, "中期", "合体·中期", 26, 83780000, 565000, 455000, 365000, 70),
    ("heti", 7, "合体", "late", 3, "后期", "合体·后期", 27, 100270000, 715000, 570000, 455000, 70),
    ("heti", 7, "合体", "perfect", 4, "圆满", "合体·圆满", 28, 120000000, 900000, 720000, 570000, 70),
    ("dacheng", 8, "大乘", "early", 1, "前期", "大乘·前期", 29, 180000000, 1200000, 960000, 770000, 80),
    ("dacheng", 8, "大乘", "mid", 2, "中期", "大乘·中期", 30, 218050000, 1420000, 1140000, 910000, 80),
    ("dacheng", 8, "大乘", "late", 3, "后期", "大乘·后期", 31, 264150000, 1690000, 1350000, 1080000, 80),
    ("dacheng", 8, "大乘", "perfect", 4, "圆满", "大乘·圆满", 32, 320000000, 2000000, 1600000, 1280000, 80),
    ("dujie", 9, "渡劫", "early", 1, "前期", "渡劫·前期", 33, 360000000, 2400000, 1920000, 1550000, 90),
    ("dujie", 9, "渡劫", "mid", 2, "中期", "渡劫·中期", 34, 396230000, 2590000, 2070000, 1660000, 90),
    ("dujie", 9, "渡劫", "late", 3, "后期", "渡劫·后期", 35, 436110000, 2780000, 2230000, 1780000, 90),
    ("dujie", 9, "渡劫", "perfect", 4, "圆满", "渡劫·圆满", 36, 480000000, 3000000, 2400000, 1900000, 90),
    # 伪仙 (过渡境界)
    ("weixian", 10, "伪仙", "early", 1, "前期", "伪仙·前期", 37, 600000000, 4500000, 3600000, 2880000, 100),
    ("weixian", 10, "伪仙", "mid", 2, "中期", "伪仙·中期", 38, 750000000, 5600000, 4480000, 3580000, 100),
    ("weixian", 10, "伪仙", "late", 3, "后期", "伪仙·后期", 39, 930000000, 6900000, 5520000, 4420000, 100),
    ("weixian", 10, "伪仙", "perfect", 4, "圆满", "伪仙·圆满", 40, 1200000000, 8500000, 6800000, 5440000, 100),
]

REALM_STAGES = tuple(RealmStage(*row) for row in _ROWS)
REALM_BY_GLOBAL = {stage.global_stage_index: stage for stage in REALM_STAGES}
REALM_BY_KEYS = {(stage.realm_key, stage.stage_key): stage for stage in REALM_STAGES}


def get_stage(realm_key: str, stage_key: str) -> RealmStage:
    return REALM_BY_KEYS[(realm_key, stage_key)]


def get_stage_by_global(global_stage_index: int) -> RealmStage:
    return REALM_BY_GLOBAL[global_stage_index]


def get_next_stage(stage: RealmStage) -> RealmStage | None:
    return REALM_BY_GLOBAL.get(stage.global_stage_index + 1)


def get_stage_for_floor(floor: int) -> RealmStage:
    if floor <= 0:
        return REALM_BY_GLOBAL[1]
    stage_index = min(len(REALM_STAGES), ((floor - 1) // 25) + 1)
    return REALM_BY_GLOBAL[stage_index]
