from __future__ import annotations

SITE_TYPE_NAMES = {
    "lingshi": "灵矿",
    "soul": "魂脉",
    "cultivation": "灵泉",
}

SITE_TYPE_WEIGHTS = (
    ("lingshi", 2),
    ("cultivation", 2),
    ("soul", 1),
)

SITE_NAME_POOLS = {
    "lingshi": (
        "青云灵矿",
        "苍岩灵矿",
        "赤霄灵矿",
        "玄岳灵矿",
        "寒汐灵矿",
        "坠星灵矿",
        "流霞灵矿",
        "照夜灵矿",
    ),
    "soul": (
        "玄冥魂脉",
        "太阴魂脉",
        "惊澜魂脉",
        "紫魄魂脉",
        "流火魂脉",
        "九幽魂脉",
        "鸣雷魂脉",
        "青魄魂脉",
    ),
    "cultivation": (
        "太乙灵泉",
        "落霞灵泉",
        "玉衡灵泉",
        "天璃灵泉",
        "听潮灵泉",
        "赤华灵泉",
        "清虚灵泉",
        "扶风灵泉",
    ),
}
