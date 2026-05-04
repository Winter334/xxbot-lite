"""证道战场静态数据与常量。"""

from __future__ import annotations

# -- 进入与生命周期 --

PG_ENTRY_QI_COST = 3
"""进入证道战场消耗气机数量。"""

PG_RECOVERY_TIMEOUT_HOURS = 2
"""断线恢复时限（小时）。超时后 run 标记为 expired。"""

PG_SCENE_TAG = "scene_proving_ground"
"""战场战斗 scene_tag。"""


# -- 地图结构 --

PG_MAP_LAYERS = 20
"""地图总层数（不含 BOSS 层）。"""

PG_NODES_PER_LAYER_MIN = 2
PG_NODES_PER_LAYER_MAX = 3
"""每层节点数范围。"""

PG_NODE_TYPE_NORMAL = "normal"
PG_NODE_TYPE_ELITE = "elite"
PG_NODE_TYPE_EVENT = "event"
PG_NODE_TYPE_BOSS = "boss"
PG_NODE_TYPE_START = "start"

PG_NODE_TYPE_WEIGHTS: dict[str, int] = {
    PG_NODE_TYPE_NORMAL: 55,
    PG_NODE_TYPE_EVENT: 28,
    PG_NODE_TYPE_ELITE: 17,
}
"""普通层节点类型生成权重（精英每张地图保底 2-3 个，超出后按权重）。"""

PG_ELITE_GUARANTEED_PER_MAP = 5
"""每张地图至少保证的精英节点数量下限（生成时优先放置）。"""


# -- BOSS 类型 --

PG_BOSS_PRESET = "preset"
PG_BOSS_STRONGEST = "strongest"
PG_BOSS_SELF = "self"

PG_BOSS_TYPES_REQUIRED = (PG_BOSS_PRESET, PG_BOSS_STRONGEST, PG_BOSS_SELF)
"""伪仙突破前必须各击败一次的三种 BOSS。"""

PG_BOSS_DISPLAY_NAMES: dict[str, str] = {
    PG_BOSS_PRESET: "天劫化身",
    PG_BOSS_STRONGEST: "道心投影",
    PG_BOSS_SELF: "心魔映射",
}


# -- 战斗数值 --

PG_BASE_STATS = {
    "atk": 3_000_000,
    "def": 2_400_000,
    "agi": 1_900_000,
    "max_hp": 24_000_000,  # def * 10
}
"""战场内固定基础面板（剥离命格/游历/法宝强化等所有加成的渡劫圆满裸数值）。"""

PG_NORMAL_MULTIPLIER_BY_LAYER: dict[int, float] = {
    1: 0.55,
    2: 0.62,
    3: 0.69,
    4: 0.76,
    5: 0.83,
    6: 0.90,
    7: 0.97,
    8: 1.04,
    9: 1.11,
    10: 1.18,
    11: 1.25,
    12: 1.32,
    13: 1.40,
    14: 1.48,
    15: 1.56,
    16: 1.64,
    17: 1.73,
    18: 1.82,
    19: 1.92,
    20: 2.02,
}
"""普通怪三维难度系数（按所在层）。"""

PG_NORMAL_AFFIX_COUNT_BY_LAYER: dict[int, int] = {
    1: 0,
    2: 0,
    3: 0,
    4: 1,
    5: 1,
    6: 1,
    7: 1,
    8: 2,
    9: 2,
    10: 2,
    11: 2,
    12: 3,
    13: 3,
    14: 3,
    15: 3,
    16: 4,
    17: 4,
    18: 4,
    19: 4,
    20: 5,
}
"""普通怪携带词条数量（按层递增）。"""

PG_ELITE_MULTIPLIER_RANGE = (1.30, 1.50)
"""精英怪相对于同层普通怪的数值倍率范围。"""

PG_ELITE_AFFIX_BONUS = 1
"""精英怪在同层普通怪基础上额外携带的词条数。"""

PG_BOSS_PRESET_AFFIX_COUNT = 5
"""固定预设 BOSS（天劫化身）携带的满 roll 词条数。"""


# -- 局外永久投资 --

PG_INVEST_STAT_BOOST_PCT_PER_LEVEL = 4
"""每级"强化体魄"提供的三维加成百分比（满级 +40%）。"""

PG_INVEST_STAT_BOOST_MAX_LEVEL = 10
"""强化体魄最大等级。"""

PG_INVEST_STAT_COSTS: tuple[int, ...] = (
    500, 1_000, 2_000, 4_000, 7_000,
    12_000, 20_000, 32_000, 50_000, 80_000,
)
"""强化体魄第 1~10 级各自的灵石花费（指数增长）。"""

PG_INVEST_AFFIX_SLOT_MAX = 2
"""初始词条槽位最大解锁数。"""

PG_INVEST_AFFIX_COSTS: tuple[int, ...] = (15_000, 40_000)
"""解锁第 1、2 个初始词条槽位的灵石花费。"""

PG_INVEST_SPIRIT_COST: int = 30_000
"""解锁初始器灵槽位的灵石花费。"""


# -- 词条/器灵操作次数 --

PG_AFFIX_PICK_OPTIONS = 3
"""每次词条获取展示的选项数量（3 选 1）。"""

PG_NORMAL_REWARD_AFFIX_OPS = 1
"""击败普通怪获得的词条操作次数。"""

PG_ELITE_REWARD_AFFIX_OPS = 1
PG_ELITE_REWARD_SPIRIT_OPS = 1
"""击败精英怪获得的词条 + 器灵操作次数。"""


# -- 积分 --

PG_SCORE_NORMAL_KILL = 10
PG_SCORE_ELITE_KILL = 30
PG_SCORE_BOSS_KILL = 100
PG_SCORE_EVENT_RISK_BONUS = 10
PG_SCORE_NO_DAMAGE_BONUS = 5

PG_DAO_TRACE_REWARD_PER_BOSS = 1
"""通关一次 BOSS 战获得的道痕数量。"""


# -- 红尘历劫 --

PG_RED_DUST_THRESHOLD = 9
"""累计触发红尘历劫达到此次数后解锁特殊荣誉。"""


# -- 状态 --

PG_STATUS_RUNNING = "running"
PG_STATUS_COMPLETED = "completed"
PG_STATUS_FAILED = "failed"
PG_STATUS_EXPIRED = "expired"
