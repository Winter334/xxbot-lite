from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceSiteDefinition:
    site_key: str
    site_name: str
    site_type: str


SITE_TYPE_NAMES = {
    "lingshi": "灵矿",
    "soul": "魂脉",
    "cultivation": "灵泉",
}

RESOURCE_SITE_DEFINITIONS = (
    ResourceSiteDefinition("qingyun_lingkuang", "青云灵矿", "lingshi"),
    ResourceSiteDefinition("cangyan_lingkuang", "苍岩灵矿", "lingshi"),
    ResourceSiteDefinition("xuanming_hunmai", "玄冥魂脉", "soul"),
    ResourceSiteDefinition("taiyi_lingquan", "太乙灵泉", "cultivation"),
    ResourceSiteDefinition("luoxia_lingquan", "落霞灵泉", "cultivation"),
)
