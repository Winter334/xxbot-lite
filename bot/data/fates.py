from __future__ import annotations

from dataclasses import dataclass


RARITY_NAMES = {
    "normal": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legendary": "传说",
}

RARITY_WEIGHTS = {
    "normal": 42,
    "rare": 30,
    "epic": 18,
    "legendary": 10,
}


def _format_signed_percent(basis_points: int) -> str:
    percent = abs(basis_points) / 100
    sign = "+" if basis_points > 0 else "-"
    return f"{sign}{percent:g}%"


@dataclass(frozen=True, slots=True)
class FateDefinition:
    key: str
    name: str
    rarity: str
    flavor_text: str
    atk_basis_points: int = 0
    def_basis_points: int = 0
    agi_basis_points: int = 0
    idle_cultivation_basis_points: int = 0
    system_soul_basis_points: int = 0
    damage_dealt_basis_points: int = 0
    damage_taken_basis_points: int = 0
    damage_reduction_basis_points: int = 0
    versus_higher_realm_damage_basis_points: int = 0
    broadcast_on_obtain: bool = False
    enabled: bool = True

    @property
    def rarity_name(self) -> str:
        return RARITY_NAMES[self.rarity]

    def effect_summary(self) -> str:
        parts: list[str] = []
        stat_parts = []
        for label, value in (("杀伐", self.atk_basis_points), ("护体", self.def_basis_points), ("身法", self.agi_basis_points)):
            if value:
                stat_parts.append(f"{label} {_format_signed_percent(value)}")
        parts.extend(stat_parts)
        if self.idle_cultivation_basis_points:
            parts.append(f"挂机/闭关修为 {_format_signed_percent(self.idle_cultivation_basis_points)}")
        if self.system_soul_basis_points:
            parts.append(f"器魂获取 {_format_signed_percent(self.system_soul_basis_points)}")
        if self.damage_dealt_basis_points:
            parts.append(f"最终伤害 {_format_signed_percent(self.damage_dealt_basis_points)}")
        if self.damage_reduction_basis_points:
            parts.append(f"最终承伤 -{self.damage_reduction_basis_points / 100:g}%")
        if self.damage_taken_basis_points:
            parts.append(f"承伤 +{self.damage_taken_basis_points / 100:g}%")
        if self.versus_higher_realm_damage_basis_points:
            parts.append(f"对高于自身大境界目标最终伤害 +{self.versus_higher_realm_damage_basis_points / 100:g}%")
        return "，".join(parts)


FATE_DEFINITIONS = (
    FateDefinition("lingtaijingshou", "灵台静守", "normal", "灵台守一，久坐自见山中真气。", idle_cultivation_basis_points=1800),
    FateDefinition("yuhucangzhen", "玉壶藏真", "rare", "壶中似有旧日月，吐纳之间藏真火。", idle_cultivation_basis_points=2800),
    FateDefinition("xuanluyangqi", "玄炉养气", "epic", "丹炉藏玄意，一息一转皆是修行。", idle_cultivation_basis_points=4000),
    FateDefinition("shanzeyizhen", "山泽遗珍", "normal", "山泽偶遗奇珍，法宝总比旁人多受几分温养。", system_soul_basis_points=1800),
    FateDefinition("xingqiaoyinpo", "星桥引魄", "rare", "命桥连星，散落器魂自会朝你而来。", system_soul_basis_points=2800),
    FateDefinition("ziyuanyingming", "紫垣映命", "epic", "紫垣之光落命府，宝胚最易聚魂。", system_soul_basis_points=4000),
    FateDefinition(
        "qingshuangxigu",
        "清霜洗骨",
        "epic",
        "骨冷如霜，修行与养宝皆见匀净。",
        idle_cultivation_basis_points=2500,
        system_soul_basis_points=2500,
    ),
    FateDefinition(
        "taichulingpei",
        "太初灵胚",
        "legendary",
        "似有太初余气绕身，修行养宝两不偏废。",
        idle_cultivation_basis_points=3500,
        system_soul_basis_points=3500,
        broadcast_on_obtain=True,
    ),
    FateDefinition("jinshicangfeng", "金石藏锋", "normal", "锋芒隐于骨缝，真到出手时更见狠辣。", atk_basis_points=1500),
    FateDefinition("xuanjiahumai", "玄甲护脉", "normal", "气血成甲，周身经脉自有护持。", def_basis_points=1500),
    FateDefinition("liuyunchenxi", "流云趁隙", "normal", "身似流云，步转之间最擅寻隙而进。", agi_basis_points=1500),
    FateDefinition("fengleibingzuo", "风雷并作", "rare", "风雷同脉，出手与身法俱见锐意。", atk_basis_points=1500, agi_basis_points=1500),
    FateDefinition("yuezhenxuanguan", "岳镇玄关", "rare", "岳意镇身，攻守之间皆稳而不钝。", atk_basis_points=1500, def_basis_points=1500),
    FateDefinition("xuanwuyufeng", "玄武驭风", "rare", "玄武负甲而行风脉，守势与机动并生。", def_basis_points=1500, agi_basis_points=1500),
    FateDefinition("wanxiangguiyi", "万象归一", "epic", "万象归一于身，三脉虽不偏锋却难有短板。", atk_basis_points=1200, def_basis_points=1200, agi_basis_points=1200),
    FateDefinition(
        "hunyuanbaopu",
        "混元抱朴",
        "legendary",
        "混元一炁抱于胸中，三脉共鸣如天成。",
        atk_basis_points=1800,
        def_basis_points=1800,
        agi_basis_points=1800,
        broadcast_on_obtain=True,
    ),
    FateDefinition("fushengcangzhuo", "浮生藏拙", "rare", "锋芒尽敛，反叫旁人难伤你根骨。", atk_basis_points=-1000, def_basis_points=2500),
    FateDefinition("bowulinshen", "薄雾临身", "rare", "雾影缠身，来去更快，却也更薄。", def_basis_points=-800, agi_basis_points=2500),
    FateDefinition("jinhairanxin", "烬海燃心", "epic", "心火燃得太烈，伤敌时也更容易反噬己身。", atk_basis_points=3000, damage_taken_basis_points=1000),
    FateDefinition("cangfenglianpo", "藏锋敛魄", "epic", "锋芒尽藏于宝胚，养魂更快，争斗却少了几分锐气。", atk_basis_points=-1200, system_soul_basis_points=4000),
    FateDefinition("gudengzhaogu", "孤灯照骨", "epic", "孤灯照骨，苦修日长，养宝却总慢半步。", idle_cultivation_basis_points=4000, system_soul_basis_points=-1500),
    FateDefinition("tanquanzhaoming", "贪泉照命", "epic", "命中贪泉，善聚宝魂，却难静心苦修。", idle_cultivation_basis_points=-1500, system_soul_basis_points=4000),
    FateDefinition("qishalinshen", "七杀临身", "epic", "七杀入命，攻势最烈，守势也最易露空门。", atk_basis_points=3500, def_basis_points=-1000),
    FateDefinition(
        "niguhengsheng",
        "逆骨横生",
        "legendary",
        "逆骨横生于命脉，锋势与身法俱强，却最不耐久战。",
        atk_basis_points=2500,
        def_basis_points=-1500,
        agi_basis_points=2500,
        broadcast_on_obtain=True,
    ),
    FateDefinition("taiyuechenyuan", "太岳沉渊", "rare", "命沉如岳，临战承势更稳。", damage_reduction_basis_points=1800),
    FateDefinition("tianhechaoying", "天河照影", "rare", "天河映影，出手时自带几分天光杀机。", damage_dealt_basis_points=1800),
    FateDefinition("nishuixingzhou", "逆水行舟", "epic", "越是强敌压境，越能逼出命中那点逆流之势。", versus_higher_realm_damage_basis_points=3000),
)

FATES_BY_KEY = {fate.key: fate for fate in FATE_DEFINITIONS}
