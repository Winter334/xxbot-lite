from __future__ import annotations

from dataclasses import dataclass


RARITY_NAMES = {
    "normal": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legendary": "传说",
}

RARITY_WEIGHTS = {
    "normal": 60,
    "rare": 25,
    "epic": 10,
    "legendary": 5,
}


@dataclass(frozen=True, slots=True)
class FateDefinition:
    key: str
    name: str
    rarity: str
    category: str
    affected_stats: tuple[str, ...]
    value_basis_points: int
    flavor_text: str
    broadcast_on_obtain: bool = False
    is_easter_egg: bool = False
    enabled: bool = True

    @property
    def percent(self) -> float:
        return self.value_basis_points / 10_000

    @property
    def rarity_name(self) -> str:
        return RARITY_NAMES[self.rarity]

    @property
    def per_stat_basis_points(self) -> int:
        stat_count = max(1, len(self.affected_stats))
        return self.value_basis_points // stat_count

    @property
    def per_stat_percent(self) -> float:
        return self.per_stat_basis_points / 10_000

    def effect_summary(self) -> str:
        if self.category == "combat":
            labels = {"atk": "杀伐", "def": "护体", "agi": "身法"}
            affected = " + ".join(labels[key] for key in self.affected_stats)
            percent = f"{self.per_stat_percent * 100:g}%"
            return f"{affected} +{percent}"
        percent = f"{self.percent * 100:g}%"
        if self.category == "cultivation":
            return f"挂机修为 +{percent}"
        return f"首通额外掉落率 +{percent}"


FATE_DEFINITIONS = (
    FateDefinition("luhuozhaogu", "炉火照骨", "normal", "combat", ("atk",), 300, "心火未熄，出手多三分烈。"),
    FateDefinition("panshishouzhong", "磐石守中", "normal", "combat", ("def",), 300, "骨重如岳，守势自成。"),
    FateDefinition("jiangfengguoxi", "江风过隙", "normal", "combat", ("agi",), 300, "身随风动，踪迹难寻。"),
    FateDefinition("shengjianzhuan", "生机暗转", "normal", "cultivation", ("idle_cultivation",), 300, "不争一时，却总比旁人多得几分修为。"),
    FateDefinition("shizhongcangyu", "石中藏玉", "normal", "fortune", ("first_clear_bonus_drop",), 300, "凡石之中，亦有藏珍之机。"),
    FateDefinition("longhubingqu", "龙虎并驱", "rare", "combat", ("atk", "def"), 600, "龙虎同走经脉，攻守俱有锐气。"),
    FateDefinition("zhenleizhuying", "震雷逐影", "rare", "combat", ("atk", "agi"), 600, "雷走九天，势若追影。"),
    FateDefinition("xuanwuyufeng", "玄武驭风", "rare", "combat", ("def", "agi"), 600, "守中带疾，动静难测。"),
    FateDefinition("kanshuiguiyuan", "坎水归元", "rare", "cultivation", ("idle_cultivation",), 600, "修行为水，绵密不绝。"),
    FateDefinition("qingluanxianshu", "青鸾衔书", "rare", "fortune", ("first_clear_bonus_drop",), 600, "青鸾报喜，常有意外机缘。"),
    FateDefinition("pojuntadou", "破军踏斗", "epic", "combat", ("atk",), 900, "命带破军，杀机先至。"),
    FateDefinition("canglongfuyue", "苍龙负岳", "epic", "combat", ("def",), 900, "重岳在肩，亦可步步向前。"),
    FateDefinition("qingfenghuaxing", "清风化形", "epic", "combat", ("agi",), 900, "动若清风，落点难寻。"),
    FateDefinition("hongchenlianxin", "红尘炼心", "epic", "cultivation", ("idle_cultivation",), 900, "万象皆劫，磨得道心更凝。"),
    FateDefinition("tianluchuiguang", "天禄垂光", "epic", "fortune", ("first_clear_bonus_drop",), 900, "天禄垂照，常得偏门好处。"),
    FateDefinition("hunyuanwugou", "混元无垢", "legendary", "combat", ("atk", "def", "agi"), 1200, "一身混元气，三脉并强。", True),
    FateDefinition("taixudaopei", "太虚道胚", "legendary", "cultivation", ("idle_cultivation",), 1200, "似是天生道种，吐纳之间便见差距。", True, True),
    FateDefinition("ziweichuizhao", "紫微垂照", "legendary", "fortune", ("first_clear_bonus_drop",), 1200, "星垂命府，机缘总会先落你身上。", True),
)

FATES_BY_KEY = {fate.key: fate for fate in FATE_DEFINITIONS}
