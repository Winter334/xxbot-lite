"""证道战场核心 Service。

包含地图生成、构筑系统、敌人/BOSS 战斗、问号事件等全部逻辑。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from bot.data.artifact_affixes import (
    ARTIFACT_AFFIX_DEFINITIONS,
    ArtifactAffixEntry,
)
from bot.data.proving_ground import (
    PG_BASE_STATS,
    PG_BOSS_PRESET,
    PG_BOSS_PRESET_AFFIX_COUNT,
    PG_BOSS_SELF,
    PG_BOSS_STRONGEST,
    PG_BOSS_TYPES_REQUIRED,
    PG_DAO_TRACE_REWARD_PER_BOSS,
    PG_ELITE_AFFIX_BONUS,
    PG_ELITE_GUARANTEED_PER_MAP,
    PG_ELITE_MULTIPLIER_RANGE,
    PG_ELITE_REWARD_AFFIX_OPS,
    PG_ELITE_REWARD_SPIRIT_OPS,
    PG_ENTRY_QI_COST,
    PG_INVEST_AFFIX_COSTS,
    PG_INVEST_AFFIX_SLOT_MAX,
    PG_INVEST_SPIRIT_COST,
    PG_INVEST_STAT_BOOST_MAX_LEVEL,
    PG_INVEST_STAT_BOOST_PCT_PER_LEVEL,
    PG_INVEST_STAT_COSTS,
    PG_MAP_LAYERS,
    PG_NODE_TYPE_BOSS,
    PG_NODE_TYPE_ELITE,
    PG_NODE_TYPE_EVENT,
    PG_NODE_TYPE_NORMAL,
    PG_NODE_TYPE_START,
    PG_NODE_TYPE_WEIGHTS,
    PG_NODES_PER_LAYER_MAX,
    PG_NODES_PER_LAYER_MIN,
    PG_NORMAL_AFFIX_COUNT_BY_LAYER,
    PG_NORMAL_MULTIPLIER_BY_LAYER,
    PG_NORMAL_REWARD_AFFIX_OPS,
    PG_RECOVERY_TIMEOUT_HOURS,
    PG_SCENE_TAG,
    PG_SCORE_BOSS_KILL,
    PG_SCORE_ELITE_KILL,
    PG_SCORE_EVENT_RISK_BONUS,
    PG_SCORE_NO_DAMAGE_BONUS,
    PG_SCORE_NORMAL_KILL,
    PG_STATUS_COMPLETED,
    PG_STATUS_EXPIRED,
    PG_STATUS_FAILED,
    PG_STATUS_RUNNING,
    PG_AFFIX_PICK_OPTIONS,
    PG_RED_DUST_THRESHOLD,
)
from bot.data.proving_ground_enemies import (
    PG_BOSS_PRESET_NAME,
    PG_ELITE_ENEMIES,
    PG_ELITE_PREFIX,
    PG_NORMAL_ENEMIES,
)
from bot.data.spirits import (
    SPIRIT_POWER_DEFINITIONS,
    SPIRIT_TIER_DEFINITIONS,
    SpiritPowerEntry,
)
from bot.models.character import Character
from bot.models.proving_ground_run import ProvingGroundRun
from bot.services.combat_service import BattleResult, CombatService, CombatantSnapshot
from bot.utils.time_utils import now_shanghai


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MapNode:
    """地图上的单个节点。"""

    node_id: int
    layer: int
    node_type: str  # start / normal / elite / event / boss
    connections: tuple[int, ...]  # 下一层可达节点 ID

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "layer": self.layer,
            "node_type": self.node_type,
            "connections": list(self.connections),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MapNode:
        return cls(
            node_id=data["node_id"],
            layer=data["layer"],
            node_type=data["node_type"],
            connections=tuple(data["connections"]),
        )


@dataclass(slots=True)
class PGBuild:
    """战场内构筑状态。"""

    atk: int = 0
    defense: int = 0
    agility: int = 0
    max_hp: int = 0
    atk_pct_bonus: int = 0  # 百分比加成（灵石投资+事件）
    def_pct_bonus: int = 0
    agi_pct_bonus: int = 0
    hp_pct_bonus: int = 0
    affixes: list[ArtifactAffixEntry] = field(default_factory=list)
    spirit_power: SpiritPowerEntry | None = None
    spirit_tier: str = ""

    def effective_atk(self) -> int:
        return max(1, int(self.atk * (100 + self.atk_pct_bonus) / 100))

    def effective_def(self) -> int:
        return max(1, int(self.defense * (100 + self.def_pct_bonus) / 100))

    def effective_agi(self) -> int:
        return max(1, int(self.agility * (100 + self.agi_pct_bonus) / 100))

    def effective_max_hp(self) -> int:
        base = self.effective_def() * 10
        return max(1, int(base * (100 + self.hp_pct_bonus) / 100))

    def to_dict(self) -> dict:
        return {
            "atk": self.atk,
            "defense": self.defense,
            "agility": self.agility,
            "max_hp": self.max_hp,
            "atk_pct_bonus": self.atk_pct_bonus,
            "def_pct_bonus": self.def_pct_bonus,
            "agi_pct_bonus": self.agi_pct_bonus,
            "hp_pct_bonus": self.hp_pct_bonus,
            "affixes": [a.to_payload() for a in self.affixes],
            "spirit_power": self.spirit_power.to_payload() if self.spirit_power else None,
            "spirit_tier": self.spirit_tier,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PGBuild:
        affixes = [
            ArtifactAffixEntry(slot=a["slot"], affix_id=a["affix_id"], rolls=a["rolls"])
            for a in data.get("affixes", [])
        ]
        sp_data = data.get("spirit_power")
        spirit_power = SpiritPowerEntry(sp_data["power_id"], sp_data["rolls"]) if sp_data else None
        return cls(
            atk=data.get("atk", 0),
            defense=data.get("defense", 0),
            agility=data.get("agility", 0),
            max_hp=data.get("max_hp", 0),
            atk_pct_bonus=data.get("atk_pct_bonus", 0),
            def_pct_bonus=data.get("def_pct_bonus", 0),
            agi_pct_bonus=data.get("agi_pct_bonus", 0),
            hp_pct_bonus=data.get("hp_pct_bonus", 0),
            affixes=affixes,
            spirit_power=spirit_power,
            spirit_tier=data.get("spirit_tier", ""),
        )


@dataclass(frozen=True, slots=True)
class PGMap:
    """完整的证道战场地图。"""

    nodes: tuple[MapNode, ...]
    boss_node_id: int

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "boss_node_id": self.boss_node_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PGMap:
        nodes = tuple(MapNode.from_dict(n) for n in data["nodes"])
        return cls(nodes=nodes, boss_node_id=data["boss_node_id"])

    def get_node(self, node_id: int) -> MapNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_layer_nodes(self, layer: int) -> list[MapNode]:
        return [n for n in self.nodes if n.layer == layer]


# ---------------------------------------------------------------------------
# 结果 dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PGEnterResult:
    success: bool
    message: str
    run: ProvingGroundRun | None = None
    pg_map: PGMap | None = None


@dataclass(slots=True)
class PGNodeResult:
    """推进到某个节点后的结果。"""

    success: bool
    message: str
    node_type: str = ""
    # 战斗结果（普通/精英/BOSS）
    battle: BattleResult | None = None
    enemy_name: str = ""
    victory: bool = False
    # 奖励
    score_gained: int = 0
    affix_ops_gained: int = 0
    spirit_ops_gained: int = 0
    # 事件结果
    event: PGEvent | None = None
    # run 是否结束
    run_ended: bool = False
    run_status: str = ""


@dataclass(frozen=True, slots=True)
class PGEvent:
    """问号事件。"""

    event_id: str
    name: str
    description: str
    category: str  # attribute / affix / spirit / mixed / easter_egg
    choices: tuple[PGEventChoice, ...] = ()
    auto_apply: bool = False  # 无选择型直接生效


@dataclass(frozen=True, slots=True)
class PGEventChoice:
    """事件选项。"""

    choice_id: str
    label: str
    description: str
    risk: bool = False


@dataclass(slots=True)
class PGEventResult:
    """事件选择后的结果。"""

    success: bool
    message: str
    score_gained: int = 0


@dataclass(slots=True)
class PGSettlement:
    """一次运行的最终结算。"""

    victory: bool
    total_score: int
    dao_traces_gained: int
    boss_type: str
    boss_killed: bool
    honor_gained: str | None = None


# ---------------------------------------------------------------------------
# Service 本体
# ---------------------------------------------------------------------------

MAX_AFFIX_SLOTS = 5


class ProvingGroundService:
    """证道战场核心逻辑。"""

    def __init__(
        self,
        combat_service: CombatService,
        rng: random.Random | None = None,
    ) -> None:
        self.combat_service = combat_service
        self.rng = rng or random.Random()

    # -----------------------------------------------------------------------
    # 地图生成
    # -----------------------------------------------------------------------

    def generate_map(self) -> PGMap:
        """生成杀戮尖塔式分叉路径地图。"""
        node_id_counter = 0
        all_nodes: list[MapNode] = []

        # 起点节点（layer 0）
        start_node = MapNode(
            node_id=node_id_counter,
            layer=0,
            node_type=PG_NODE_TYPE_START,
            connections=(),
        )
        all_nodes.append(start_node)
        node_id_counter += 1

        # 生成每层节点
        layers: list[list[MapNode]] = []
        for layer_idx in range(1, PG_MAP_LAYERS + 1):
            count = self.rng.randint(PG_NODES_PER_LAYER_MIN, PG_NODES_PER_LAYER_MAX)
            layer_nodes: list[MapNode] = []
            for _ in range(count):
                node = MapNode(
                    node_id=node_id_counter,
                    layer=layer_idx,
                    node_type="",  # 类型稍后分配
                    connections=(),
                )
                layer_nodes.append(node)
                node_id_counter += 1
            layers.append(layer_nodes)

        # BOSS 节点（最后一层之后）
        boss_node = MapNode(
            node_id=node_id_counter,
            layer=PG_MAP_LAYERS + 1,
            node_type=PG_NODE_TYPE_BOSS,
            connections=(),
        )

        # 连接：每个节点至少连接下一层 1 个节点，最多 2 个
        # 起点 → 第 1 层
        first_layer = layers[0]
        start_connections = self._assign_connections(
            [start_node], first_layer,
        )
        start_node = MapNode(
            node_id=start_node.node_id,
            layer=start_node.layer,
            node_type=start_node.node_type,
            connections=start_connections[start_node.node_id],
        )
        all_nodes[0] = start_node

        # 中间各层互连
        for i in range(len(layers) - 1):
            current_layer = layers[i]
            next_layer = layers[i + 1]
            conn_map = self._assign_connections(current_layer, next_layer)
            # 更新当前层节点的 connections
            for j, node in enumerate(current_layer):
                current_layer[j] = MapNode(
                    node_id=node.node_id,
                    layer=node.layer,
                    node_type=node.node_type,
                    connections=conn_map[node.node_id],
                )

        # 最后一层 → BOSS
        last_layer = layers[-1]
        for j, node in enumerate(last_layer):
            last_layer[j] = MapNode(
                node_id=node.node_id,
                layer=node.layer,
                node_type=node.node_type,
                connections=(boss_node.node_id,),
            )

        # 分配节点类型（精英保底 + 权重随机）
        all_non_terminal: list[tuple[int, int, MapNode]] = []
        for layer_idx, layer in enumerate(layers):
            for node_idx, node in enumerate(layer):
                all_non_terminal.append((layer_idx, node_idx, node))

        # 精英保底放置
        elite_placed = 0
        elite_candidates = list(range(len(all_non_terminal)))
        self.rng.shuffle(elite_candidates)
        for idx in elite_candidates:
            if elite_placed >= PG_ELITE_GUARANTEED_PER_MAP:
                break
            li, ni, node = all_non_terminal[idx]
            layers[li][ni] = MapNode(
                node_id=node.node_id,
                layer=node.layer,
                node_type=PG_NODE_TYPE_ELITE,
                connections=node.connections,
            )
            all_non_terminal[idx] = (li, ni, layers[li][ni])
            elite_placed += 1

        # 剩余节点按权重分配
        type_keys = list(PG_NODE_TYPE_WEIGHTS.keys())
        type_weights = list(PG_NODE_TYPE_WEIGHTS.values())
        for li, ni, node in all_non_terminal:
            if layers[li][ni].node_type != "":
                continue  # 已分配（精英保底）
            chosen = self.rng.choices(type_keys, weights=type_weights, k=1)[0]
            layers[li][ni] = MapNode(
                node_id=node.node_id,
                layer=node.layer,
                node_type=chosen,
                connections=node.connections,
            )

        # 汇总
        all_nodes_final: list[MapNode] = [all_nodes[0]]  # start
        for layer in layers:
            all_nodes_final.extend(layer)
        all_nodes_final.append(boss_node)

        return PGMap(
            nodes=tuple(all_nodes_final),
            boss_node_id=boss_node.node_id,
        )

    def _assign_connections(
        self,
        current_layer: list[MapNode],
        next_layer: list[MapNode],
    ) -> dict[int, tuple[int, ...]]:
        """为当前层每个节点分配到下一层的连接，保证全覆盖。"""
        next_ids = [n.node_id for n in next_layer]
        conn_map: dict[int, list[int]] = {n.node_id: [] for n in current_layer}
        connected_next: set[int] = set()

        # 每个当前节点至少连 1 个
        for node in current_layer:
            target = self.rng.choice(next_ids)
            conn_map[node.node_id].append(target)
            connected_next.add(target)

        # 确保下一层每个节点至少被 1 个上层节点连接
        for nid in next_ids:
            if nid not in connected_next:
                parent = self.rng.choice(current_layer)
                conn_map[parent.node_id].append(nid)
                connected_next.add(nid)

        # 随机额外连接（每个节点最多连 2 个）
        for node in current_layer:
            if len(conn_map[node.node_id]) < 2 and self.rng.random() < 0.5:
                available = [nid for nid in next_ids if nid not in conn_map[node.node_id]]
                if available:
                    conn_map[node.node_id].append(self.rng.choice(available))

        return {nid: tuple(sorted(set(targets))) for nid, targets in conn_map.items()}

    # -----------------------------------------------------------------------
    # BOSS 类型选择
    # -----------------------------------------------------------------------

    def pick_boss_type(self, character: Character) -> str:
        """根据角色已击败记录选择 BOSS 类型。"""
        killed = set(character.stored_pg_boss_kills())
        # 前 3 次必须各面对不同类型
        for boss_type in PG_BOSS_TYPES_REQUIRED:
            if boss_type not in killed:
                return boss_type
        # 全部击败后随机
        return self.rng.choice(PG_BOSS_TYPES_REQUIRED)

    # -----------------------------------------------------------------------
    # 构筑初始化
    # -----------------------------------------------------------------------

    def create_initial_build(self, character: Character | None = None) -> PGBuild:
        """创建初始构筑，应用角色的永久投资加成。"""
        stat_bonus = (character.pg_invest_stat_level * PG_INVEST_STAT_BOOST_PCT_PER_LEVEL) if character else 0
        # 初始词条（按解锁槽位数随机 roll）
        affix_slots = character.pg_invest_affix_slots if character else 0
        affixes = [
            self._roll_random_affix(slot=i)
            for i in range(affix_slots)
        ]
        # 初始器灵（解锁后随机 roll）
        spirit_power: SpiritPowerEntry | None = None
        spirit_tier = ""
        if character and character.pg_invest_spirit_unlocked:
            spirit_tier, spirit_power = self._roll_random_spirit()
        return PGBuild(
            atk=PG_BASE_STATS["atk"],
            defense=PG_BASE_STATS["def"],
            agility=PG_BASE_STATS["agi"],
            max_hp=PG_BASE_STATS["max_hp"],
            atk_pct_bonus=stat_bonus,
            def_pct_bonus=stat_bonus,
            agi_pct_bonus=stat_bonus,
            affixes=affixes,
            spirit_power=spirit_power,
            spirit_tier=spirit_tier,
        )

    # -----------------------------------------------------------------------
    # 序列化 / 反序列化 helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def serialize_map(pg_map: PGMap) -> str:
        return json.dumps(pg_map.to_dict(), ensure_ascii=False)

    @staticmethod
    def deserialize_map(raw: str) -> PGMap:
        return PGMap.from_dict(json.loads(raw))

    @staticmethod
    def serialize_build(build: PGBuild) -> str:
        return json.dumps(build.to_dict(), ensure_ascii=False)

    @staticmethod
    def deserialize_build(raw: str) -> PGBuild:
        data = json.loads(raw)
        if not data:
            return PGBuild()
        return PGBuild.from_dict(data)

    # -----------------------------------------------------------------------
    # 超时检查
    # -----------------------------------------------------------------------

    @staticmethod
    def is_run_expired(run: ProvingGroundRun) -> bool:
        if run.status != PG_STATUS_RUNNING:
            return False
        if run.last_action_at is None:
            return False
        from datetime import timedelta
        deadline = run.last_action_at + timedelta(hours=PG_RECOVERY_TIMEOUT_HOURS)
        return now_shanghai() > deadline

    # -----------------------------------------------------------------------
    # 构筑系统 — 灵石投资
    # -----------------------------------------------------------------------

    def invest_stat_boost(self, character: Character) -> tuple[bool, str]:
        """永久投资：强化体魄（三维各 +4%/级）。"""
        level = character.pg_invest_stat_level
        if level >= PG_INVEST_STAT_BOOST_MAX_LEVEL:
            return False, "强化体魄已达上限。"
        cost = PG_INVEST_STAT_COSTS[level]
        if character.lingshi < cost:
            return False, f"灵石不足（需要 {cost:,}，当前 {character.lingshi:,}）。"
        character.lingshi -= cost
        character.pg_invest_stat_level += 1
        new_level = character.pg_invest_stat_level
        total_bonus = new_level * PG_INVEST_STAT_BOOST_PCT_PER_LEVEL
        return True, f"体魄强化至 {new_level} 级，三维各 +{total_bonus}%。"

    def invest_starter_affix(self, character: Character) -> tuple[bool, str]:
        """永久投资：解锁初始词条槽位。"""
        slots = character.pg_invest_affix_slots
        if slots >= PG_INVEST_AFFIX_SLOT_MAX:
            return False, "初始词条槽位已全部解锁。"
        cost = PG_INVEST_AFFIX_COSTS[slots]
        if character.lingshi < cost:
            return False, f"灵石不足（需要 {cost:,}，当前 {character.lingshi:,}）。"
        character.lingshi -= cost
        character.pg_invest_affix_slots += 1
        return True, f"解锁初始词条第 {character.pg_invest_affix_slots} 槽位。"

    def invest_starter_spirit(self, character: Character) -> tuple[bool, str]:
        """永久投资：解锁初始器灵槽位。"""
        if character.pg_invest_spirit_unlocked:
            return False, "初始器灵已解锁。"
        if character.lingshi < PG_INVEST_SPIRIT_COST:
            return False, f"灵石不足（需要 {PG_INVEST_SPIRIT_COST:,}，当前 {character.lingshi:,}）。"
        character.lingshi -= PG_INVEST_SPIRIT_COST
        character.pg_invest_spirit_unlocked = True
        return True, "解锁初始器灵槽位。"

    # -----------------------------------------------------------------------
    # 构筑系统 — 词条操作
    # -----------------------------------------------------------------------

    def roll_affix_choices(self, count: int = 3) -> list[ArtifactAffixEntry]:
        """随机生成 N 个候选词条供玩家 3 选 1。"""
        choices = []
        used_ids: set[str] = set()
        for i in range(count):
            entry = self._roll_random_affix(slot=0)
            # 尽量不重复，最多重试 5 次
            for _ in range(5):
                if entry.affix_id not in used_ids:
                    break
                entry = self._roll_random_affix(slot=0)
            used_ids.add(entry.affix_id)
            choices.append(entry)
        return choices

    def apply_affix_pick(self, build: PGBuild, entry: ArtifactAffixEntry) -> str:
        """玩家选择了一个词条，加入构筑。"""
        if len(build.affixes) >= MAX_AFFIX_SLOTS:
            return "词条槽已满，无法添加。"
        # 重新设置 slot 为当前索引
        new_entry = ArtifactAffixEntry(
            slot=len(build.affixes),
            affix_id=entry.affix_id,
            rolls=entry.rolls,
        )
        build.affixes.append(new_entry)
        defn = self._get_affix_definition(entry.affix_id)
        name = defn.name if defn else entry.affix_id
        return f"装备词条「{name}」。"

    def reroll_affix(self, build: PGBuild, slot: int) -> str:
        """强化已有词条（重新 roll 数值取高值）。"""
        if slot < 0 or slot >= len(build.affixes):
            return "无效的词条槽位。"
        old = build.affixes[slot]
        defn = self._get_affix_definition(old.affix_id)
        if defn is None:
            return "词条定义未找到。"
        new_rolls = defn.roll(self.rng)
        # 取高值
        merged = {
            key: max(old.rolls.get(key, 0), new_rolls.get(key, 0))
            for key in set(old.rolls) | set(new_rolls)
        }
        build.affixes[slot] = ArtifactAffixEntry(
            slot=slot, affix_id=old.affix_id, rolls=merged,
        )
        return f"词条「{defn.name}」强化成功。"

    # -----------------------------------------------------------------------
    # 构筑系统 — 器灵操作
    # -----------------------------------------------------------------------

    def roll_new_spirit(self, build: PGBuild) -> tuple[str, SpiritPowerEntry]:
        """随机获得新器灵（替换或首次）。"""
        tier, power = self._roll_random_spirit()
        build.spirit_power = power
        build.spirit_tier = tier
        power_defn = self._get_power_definition(power.power_id)
        name = power_defn.name if power_defn else power.power_id
        return f"获得器灵神通「{name}」。", power

    def reroll_spirit(self, build: PGBuild) -> str:
        """强化当前器灵（重 roll 数值取高）。"""
        if build.spirit_power is None:
            return "当前没有器灵。"
        defn = self._get_power_definition(build.spirit_power.power_id)
        if defn is None:
            return "器灵定义未找到。"
        new_entry = defn.roll(build.spirit_tier or "mid", self.rng)
        merged = {
            key: max(build.spirit_power.rolls.get(key, 0), new_entry.rolls.get(key, 0))
            for key in set(build.spirit_power.rolls) | set(new_entry.rolls)
        }
        build.spirit_power = SpiritPowerEntry(
            power_id=build.spirit_power.power_id, rolls=merged,
        )
        return f"器灵「{defn.name}」强化成功。"

    # -----------------------------------------------------------------------
    # 构筑 → CombatantSnapshot
    # -----------------------------------------------------------------------

    def build_player_snapshot(self, build: PGBuild, name: str) -> CombatantSnapshot:
        """从构筑状态生成战斗快照。"""
        return CombatantSnapshot(
            name=name,
            atk=build.effective_atk(),
            defense=build.effective_def(),
            agility=build.effective_agi(),
            max_hp=build.effective_max_hp(),
            affixes=tuple(build.affixes),
            spirit_power=build.spirit_power,
        )

    # -----------------------------------------------------------------------
    # 内部工具
    # -----------------------------------------------------------------------

    def _roll_random_affix(self, slot: int = 0) -> ArtifactAffixEntry:
        defn = self.rng.choice(ARTIFACT_AFFIX_DEFINITIONS)
        return ArtifactAffixEntry(slot=slot, affix_id=defn.affix_id, rolls=defn.roll(self.rng))

    def _roll_random_spirit(self) -> tuple[str, SpiritPowerEntry]:
        """随机 roll 一个器灵（含品阶和神通）。返回 (tier_key, power_entry)。"""
        tier = self.rng.choices(
            SPIRIT_TIER_DEFINITIONS,
            weights=[t.weight for t in SPIRIT_TIER_DEFINITIONS],
            k=1,
        )[0]
        power_defn = self.rng.choice(SPIRIT_POWER_DEFINITIONS)
        entry = power_defn.roll(tier.key, self.rng)
        return tier.key, entry

    @staticmethod
    def _get_affix_definition(affix_id: str):
        for defn in ARTIFACT_AFFIX_DEFINITIONS:
            if defn.affix_id == affix_id:
                return defn
        return None

    @staticmethod
    def _get_power_definition(power_id: str):
        for defn in SPIRIT_POWER_DEFINITIONS:
            if defn.power_id == power_id:
                return defn
        return None

    # -----------------------------------------------------------------------
    # 敌人生成
    # -----------------------------------------------------------------------

    def generate_normal_enemy(self, layer: int) -> tuple[str, CombatantSnapshot]:
        """生成普通怪。"""
        multiplier = PG_NORMAL_MULTIPLIER_BY_LAYER.get(layer, 1.0)
        affix_count = PG_NORMAL_AFFIX_COUNT_BY_LAYER.get(layer, 0)
        name = self.rng.choice(PG_NORMAL_ENEMIES)
        affixes = [self._roll_random_affix(slot=i) for i in range(affix_count)]
        enemy = self.combat_service.create_combatant(
            name=name,
            atk=max(1, int(PG_BASE_STATS["atk"] * multiplier)),
            defense=max(1, int(PG_BASE_STATS["def"] * multiplier)),
            agility=max(1, int(PG_BASE_STATS["agi"] * multiplier)),
            affixes=tuple(affixes),
        )
        return name, enemy

    def generate_elite_enemy(self, layer: int) -> tuple[str, CombatantSnapshot]:
        """生成精英怪：同层普通怪 × 1.3~1.5，词条+1，携带器灵。"""
        base_mult = PG_NORMAL_MULTIPLIER_BY_LAYER.get(layer, 1.0)
        elite_mult = base_mult * self.rng.uniform(*PG_ELITE_MULTIPLIER_RANGE)
        base_affix_count = PG_NORMAL_AFFIX_COUNT_BY_LAYER.get(layer, 0)
        affix_count = base_affix_count + PG_ELITE_AFFIX_BONUS
        raw_name = self.rng.choice(PG_ELITE_ENEMIES)
        name = f"{PG_ELITE_PREFIX}{raw_name}"
        affixes = [self._roll_random_affix(slot=i) for i in range(affix_count)]
        _, spirit = self._roll_random_spirit()
        enemy = self.combat_service.create_combatant(
            name=name,
            atk=max(1, int(PG_BASE_STATS["atk"] * elite_mult)),
            defense=max(1, int(PG_BASE_STATS["def"] * elite_mult)),
            agility=max(1, int(PG_BASE_STATS["agi"] * elite_mult)),
            affixes=tuple(affixes),
            spirit_power=spirit,
        )
        return name, enemy

    def generate_boss_preset(self) -> tuple[str, CombatantSnapshot]:
        """生成天劫化身 BOSS：固定高面板 + 5 满 roll 词条 + 高品阶器灵。"""
        boss_mult = 1.5
        affixes = []
        for i in range(PG_BOSS_PRESET_AFFIX_COUNT):
            defn = self.rng.choice(ARTIFACT_AFFIX_DEFINITIONS)
            # 满 roll：取每个范围的最大值
            max_rolls = {key: high for key, _low, high in defn.roll_ranges}
            affixes.append(ArtifactAffixEntry(slot=i, affix_id=defn.affix_id, rolls=max_rolls))
        # 高品阶器灵（peak 或 supreme）
        tier_key = self.rng.choice(["peak", "supreme"])
        power_defn = self.rng.choice(SPIRIT_POWER_DEFINITIONS)
        spirit = power_defn.roll(tier_key, self.rng)
        name = PG_BOSS_PRESET_NAME
        enemy = self.combat_service.create_combatant(
            name=name,
            atk=max(1, int(PG_BASE_STATS["atk"] * boss_mult)),
            defense=max(1, int(PG_BASE_STATS["def"] * boss_mult)),
            agility=max(1, int(PG_BASE_STATS["agi"] * boss_mult)),
            affixes=tuple(affixes),
            spirit_power=spirit,
        )
        return name, enemy

    def generate_boss_from_character(
        self,
        character: Character,
        character_service,
        label: str,
    ) -> tuple[str, CombatantSnapshot]:
        """生成道心投影/心魔映射 BOSS：使用真实玩家快照。"""
        snap = character_service.build_combatant(character)
        # 用真实面板构建 BOSS，名称加标签
        name = f"{label}·{snap.name}"
        enemy = CombatantSnapshot(
            name=name,
            atk=snap.atk,
            defense=snap.defense,
            agility=snap.agility,
            max_hp=snap.max_hp,
            affixes=snap.affixes,
            spirit_power=snap.spirit_power,
            realm_index=snap.realm_index,
            damage_dealt_basis_points=snap.damage_dealt_basis_points,
            damage_taken_basis_points=snap.damage_taken_basis_points,
            damage_reduction_basis_points=snap.damage_reduction_basis_points,
            versus_higher_realm_damage_basis_points=snap.versus_higher_realm_damage_basis_points,
        )
        return name, enemy

    # -----------------------------------------------------------------------
    # 节点战斗
    # -----------------------------------------------------------------------

    def run_node_combat(
        self,
        node: MapNode,
        build: PGBuild,
        player_name: str,
        run: ProvingGroundRun,
        *,
        boss_snapshot: CombatantSnapshot | None = None,
    ) -> PGNodeResult:
        """执行一个战斗节点（普通/精英/BOSS），返回结果并更新 run。"""
        player = self.build_player_snapshot(build, player_name)

        if node.node_type == PG_NODE_TYPE_BOSS:
            if boss_snapshot is None:
                return PGNodeResult(False, "BOSS 数据缺失。")
            enemy_name = boss_snapshot.name
            enemy = boss_snapshot
        elif node.node_type == PG_NODE_TYPE_ELITE:
            enemy_name, enemy = self.generate_elite_enemy(node.layer)
        else:
            enemy_name, enemy = self.generate_normal_enemy(node.layer)

        battle = self.combat_service.run_battle(
            player, enemy, scene_tags=(PG_SCENE_TAG,),
        )
        victory = battle.challenger_won
        score = 0
        affix_ops = 0
        spirit_ops = 0

        if victory:
            # 积分
            if node.node_type == PG_NODE_TYPE_BOSS:
                score = PG_SCORE_BOSS_KILL
            elif node.node_type == PG_NODE_TYPE_ELITE:
                score = PG_SCORE_ELITE_KILL
            else:
                score = PG_SCORE_NORMAL_KILL
            # 无伤奖励
            if battle.challenger_hp_after >= player.max_hp:
                score += PG_SCORE_NO_DAMAGE_BONUS
            # 奖励操作次数
            if node.node_type == PG_NODE_TYPE_ELITE:
                affix_ops = PG_ELITE_REWARD_AFFIX_OPS
                spirit_ops = PG_ELITE_REWARD_SPIRIT_OPS
            elif node.node_type == PG_NODE_TYPE_NORMAL:
                affix_ops = PG_NORMAL_REWARD_AFFIX_OPS
            # 更新 run
            run.score += score
            run.pending_affix_ops += affix_ops
            run.pending_spirit_ops += spirit_ops

        run_ended = not victory
        run_status = PG_STATUS_FAILED if not victory and node.node_type != PG_NODE_TYPE_BOSS else ""
        if victory and node.node_type == PG_NODE_TYPE_BOSS:
            run_ended = True
            run_status = PG_STATUS_COMPLETED

        return PGNodeResult(
            success=True,
            message="战斗胜利！" if victory else "战斗失败…",
            node_type=node.node_type,
            battle=battle,
            enemy_name=enemy_name,
            victory=victory,
            score_gained=score,
            affix_ops_gained=affix_ops,
            spirit_ops_gained=spirit_ops,
            run_ended=run_ended,
            run_status=run_status,
        )

    # -----------------------------------------------------------------------
    # 问号事件系统
    # -----------------------------------------------------------------------

    # 事件类别权重
    _EVENT_CATEGORY_WEIGHTS = {
        "attribute": 30,
        "affix": 25,
        "spirit": 20,
        "mixed": 15,
        "easter_egg": 10,
    }

    def generate_event(self, build: PGBuild) -> PGEvent:
        """根据权重随机生成一个问号事件。"""
        cats = list(self._EVENT_CATEGORY_WEIGHTS.keys())
        weights = list(self._EVENT_CATEGORY_WEIGHTS.values())
        category = self.rng.choices(cats, weights=weights, k=1)[0]

        generators = {
            "attribute": self._gen_attribute_event,
            "affix": self._gen_affix_event,
            "spirit": self._gen_spirit_event,
            "mixed": self._gen_mixed_event,
            "easter_egg": self._gen_easter_egg_event,
        }
        return generators[category](build)

    # -- 属性事件 --

    def _gen_attribute_event(self, build: PGBuild) -> PGEvent:
        roll = self.rng.random()
        if roll < 0.30:
            return PGEvent(
                event_id="lingquan",
                name="灵泉洗礼",
                description="一处灵泉涌现，可选择一项三维获得 15~25% 加成。",
                category="attribute",
                choices=(
                    PGEventChoice("atk", "强化杀伐", "杀伐 +15~25%"),
                    PGEventChoice("def", "强化守御", "守御 +15~25%"),
                    PGEventChoice("agi", "强化身法", "身法 +15~25%"),
                ),
            )
        if roll < 0.55:
            return PGEvent(
                event_id="tiandi_lingyun",
                name="天地灵韵",
                description="天地灵气灌体，三维各 +8%。",
                category="attribute",
                auto_apply=True,
            )
        if roll < 0.80:
            return PGEvent(
                event_id="yaoqi",
                name="妖气侵蚀",
                description="妖气侵入，一项三维 -5~8%，另一项 +15~20%。",
                category="attribute",
                choices=(
                    PGEventChoice("atk_up", "杀伐吞噬", "杀伐 +15~20%，守御 -5~8%"),
                    PGEventChoice("def_up", "守御凝聚", "守御 +15~20%，身法 -5~8%"),
                    PGEventChoice("agi_up", "身法蜕变", "身法 +15~20%，杀伐 -5~8%"),
                ),
            )
        return PGEvent(
            event_id="qixue",
            name="气血翻涌",
            description="一股磅礴气血涌入，最大生命 +20%。",
            category="attribute",
            auto_apply=True,
        )

    # -- 词条事件 --

    def _gen_affix_event(self, build: PGBuild) -> PGEvent:
        roll = self.rng.random()
        if roll < 0.30:
            return PGEvent(
                event_id="wudao",
                name="悟道机缘",
                description="获得 1 次词条抽取机会。",
                category="affix",
                auto_apply=True,
            )
        if roll < 0.55 and build.affixes:
            return PGEvent(
                event_id="fabao_gongming",
                name="法宝共鸣",
                description="选择 1 个词条强化（重 roll 取高值）。",
                category="affix",
                choices=tuple(
                    PGEventChoice(
                        f"slot_{i}",
                        self._get_affix_definition(a.affix_id).name
                        if self._get_affix_definition(a.affix_id) else a.affix_id,
                        f"强化第 {i + 1} 个词条",
                    )
                    for i, a in enumerate(build.affixes)
                ),
            )
        if roll < 0.75 and len(build.affixes) >= 2:
            return PGEvent(
                event_id="tianjie_cuilian",
                name="天劫淬炼",
                description="移除 1 个词条，剩余所有词条数值 +20%。",
                category="affix",
                choices=tuple(
                    PGEventChoice(
                        f"remove_{i}",
                        self._get_affix_definition(a.affix_id).name
                        if self._get_affix_definition(a.affix_id) else a.affix_id,
                        f"移除此词条",
                        risk=True,
                    )
                    for i, a in enumerate(build.affixes)
                ),
            )
        # 兜底：悟道机缘
        return PGEvent(
            event_id="wudao",
            name="悟道机缘",
            description="获得 1 次词条抽取机会。",
            category="affix",
            auto_apply=True,
        )

    # -- 器灵事件 --

    def _gen_spirit_event(self, build: PGBuild) -> PGEvent:
        roll = self.rng.random()
        if roll < 0.30 and build.spirit_power is not None:
            return PGEvent(
                event_id="qiling_juexing",
                name="器灵觉醒",
                description="器灵品阶提升，数值重 roll 取高。",
                category="spirit",
                auto_apply=True,
            )
        if roll < 0.55:
            return PGEvent(
                event_id="qiling_chonglian",
                name="器灵重炼",
                description="获得 1 次器灵抽取机会。",
                category="spirit",
                choices=(
                    PGEventChoice("accept", "接受新器灵", "替换当前器灵"),
                    PGEventChoice("reject", "拒绝", "保留当前器灵"),
                ),
            )
        if roll < 0.75 and build.spirit_power is not None:
            return PGEvent(
                event_id="qiling_yibian",
                name="器灵异变",
                description="器灵神通重置（保留品阶，换神通）。",
                category="spirit",
                choices=(
                    PGEventChoice("accept", "接受异变", "获得新神通"),
                    PGEventChoice("reject", "拒绝", "保留当前神通"),
                ),
            )
        if build.spirit_power is not None:
            return PGEvent(
                event_id="lingshi_event",
                name="灵蚀",
                description="器灵降 1 品，获得 2 次词条操作机会。",
                category="spirit",
                choices=(
                    PGEventChoice("accept", "接受灵蚀", "降品换词条机会", risk=True),
                    PGEventChoice("reject", "拒绝", "保留器灵品阶"),
                ),
            )
        # 无器灵时兜底
        return PGEvent(
            event_id="qiling_chonglian",
            name="器灵重炼",
            description="获得 1 次器灵抽取机会。",
            category="spirit",
            auto_apply=True,
        )

    # -- 混合/风险事件 --

    def _gen_mixed_event(self, build: PGBuild) -> PGEvent:
        roll = self.rng.random()
        if roll < 0.40:
            return PGEvent(
                event_id="duyun",
                name="赌运",
                description="50% 三维各 +15%，50% 三维各 -5%。",
                category="mixed",
                choices=(
                    PGEventChoice("gamble", "赌！", "五五开", risk=True),
                    PGEventChoice("skip", "算了", "不冒险"),
                ),
            )
        if roll < 0.70:
            return PGEvent(
                event_id="tianren_jiaozhan",
                name="天人交战",
                description="放弃所有词条操作次数，获得一个极品器灵。",
                category="mixed",
                choices=(
                    PGEventChoice("accept", "舍词条得器灵", "获得 peak/supreme 器灵", risk=True),
                    PGEventChoice("reject", "拒绝", "保留操作次数"),
                ),
            )
        return PGEvent(
            event_id="xinmo_shilian",
            name="心魔试炼",
            description="与加强版自身投影战斗，胜利获大量积分 + 1 次词条操作。",
            category="mixed",
            choices=(
                PGEventChoice("fight", "迎战心魔", "风险较高但收益丰厚", risk=True),
                PGEventChoice("skip", "退避", "不冒险"),
            ),
        )

    # -- 彩蛋事件 --

    def _gen_easter_egg_event(self, _build: PGBuild) -> PGEvent:
        return PGEvent(
            event_id="hongchen_lijie",
            name="红尘历劫",
            description="一缕红尘拂过……似有所悟，又似无所得。",
            category="easter_egg",
            auto_apply=True,
        )

    # -----------------------------------------------------------------------
    # 事件效果应用
    # -----------------------------------------------------------------------

    def apply_event(
        self,
        event: PGEvent,
        choice_id: str,
        build: PGBuild,
        run: ProvingGroundRun,
        character: Character,
    ) -> PGEventResult:
        """应用事件效果，返回结果描述。"""
        handler = {
            "lingquan": self._apply_lingquan,
            "tiandi_lingyun": self._apply_tiandi_lingyun,
            "yaoqi": self._apply_yaoqi,
            "qixue": self._apply_qixue,
            "wudao": self._apply_wudao,
            "fabao_gongming": self._apply_fabao_gongming,
            "tianjie_cuilian": self._apply_tianjie_cuilian,
            "qiling_juexing": self._apply_qiling_juexing,
            "qiling_chonglian": self._apply_qiling_chonglian,
            "qiling_yibian": self._apply_qiling_yibian,
            "lingshi_event": self._apply_lingshi,
            "duyun": self._apply_duyun,
            "tianren_jiaozhan": self._apply_tianren_jiaozhan,
            "xinmo_shilian": self._apply_xinmo_shilian,
            "hongchen_lijie": self._apply_hongchen_lijie,
        }.get(event.event_id)
        if handler is None:
            return PGEventResult(False, "未知事件。")
        return handler(choice_id, build, run, character)

    def _apply_lingquan(self, choice_id: str, build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        bonus = self.rng.randint(15, 25)
        if choice_id == "atk":
            build.atk_pct_bonus += bonus
            return PGEventResult(True, f"杀伐 +{bonus}%。")
        if choice_id == "def":
            build.def_pct_bonus += bonus
            return PGEventResult(True, f"守御 +{bonus}%。")
        build.agi_pct_bonus += bonus
        return PGEventResult(True, f"身法 +{bonus}%。")

    def _apply_tiandi_lingyun(self, _cid: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        build.atk_pct_bonus += 8
        build.def_pct_bonus += 8
        build.agi_pct_bonus += 8
        return PGEventResult(True, "天地灵韵灌体，三维各 +8%。")

    def _apply_yaoqi(self, choice_id: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        up = self.rng.randint(15, 20)
        down = self.rng.randint(5, 8)
        if choice_id == "atk_up":
            build.atk_pct_bonus += up
            build.def_pct_bonus -= down
            return PGEventResult(True, f"杀伐 +{up}%，守御 -{down}%。")
        if choice_id == "def_up":
            build.def_pct_bonus += up
            build.agi_pct_bonus -= down
            return PGEventResult(True, f"守御 +{up}%，身法 -{down}%。")
        build.agi_pct_bonus += up
        build.atk_pct_bonus -= down
        return PGEventResult(True, f"身法 +{up}%，杀伐 -{down}%。")

    def _apply_qixue(self, _cid: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        build.hp_pct_bonus += 20
        return PGEventResult(True, "气血翻涌，最大生命 +20%。")

    def _apply_wudao(self, _cid: str, _build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        run.pending_affix_ops += 1
        return PGEventResult(True, "悟道机缘，获得 1 次词条操作机会。")

    def _apply_fabao_gongming(self, choice_id: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        # choice_id = "slot_N"
        try:
            slot = int(choice_id.split("_")[1])
        except (IndexError, ValueError):
            return PGEventResult(False, "无效选择。")
        msg = self.reroll_affix(build, slot)
        return PGEventResult(True, msg)

    def _apply_tianjie_cuilian(self, choice_id: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        try:
            idx = int(choice_id.split("_")[1])
        except (IndexError, ValueError):
            return PGEventResult(False, "无效选择。")
        if idx < 0 or idx >= len(build.affixes):
            return PGEventResult(False, "无效词条。")
        removed = build.affixes.pop(idx)
        defn = self._get_affix_definition(removed.affix_id)
        removed_name = defn.name if defn else removed.affix_id
        # 剩余词条数值 +20%
        for i, a in enumerate(build.affixes):
            boosted = {k: int(v * 1.2) for k, v in a.rolls.items()}
            build.affixes[i] = ArtifactAffixEntry(slot=i, affix_id=a.affix_id, rolls=boosted)
        return PGEventResult(True, f"移除「{removed_name}」，剩余词条数值 +20%。")

    def _apply_qiling_juexing(self, _cid: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if build.spirit_power is None:
            return PGEventResult(False, "没有器灵。")
        msg = self.reroll_spirit(build)
        return PGEventResult(True, f"器灵觉醒，{msg}")

    def _apply_qiling_chonglian(self, choice_id: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "reject":
            return PGEventResult(True, "保留当前器灵。")
        msg, _ = self.roll_new_spirit(build)
        return PGEventResult(True, msg)

    def _apply_qiling_yibian(self, choice_id: str, build: PGBuild, _run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "reject":
            return PGEventResult(True, "保留当前神通。")
        if build.spirit_power is None:
            return PGEventResult(False, "没有器灵。")
        tier = build.spirit_tier or "mid"
        power_defn = self.rng.choice(SPIRIT_POWER_DEFINITIONS)
        new_power = power_defn.roll(tier, self.rng)
        build.spirit_power = new_power
        name = power_defn.name
        return PGEventResult(True, f"器灵异变，获得新神通「{name}」。")

    def _apply_lingshi(self, choice_id: str, build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "reject":
            return PGEventResult(True, "保留器灵品阶。")
        # 降品
        tier_order = ["supreme", "peak", "high", "mid", "low"]
        current = build.spirit_tier or "mid"
        idx = tier_order.index(current) if current in tier_order else 3
        if idx < len(tier_order) - 1:
            build.spirit_tier = tier_order[idx + 1]
        run.pending_affix_ops += 2
        return PGEventResult(True, f"器灵降品，获得 2 次词条操作机会。")

    def _apply_duyun(self, choice_id: str, build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "skip":
            return PGEventResult(True, "明智地选择了不冒险。")
        if self.rng.random() < 0.5:
            build.atk_pct_bonus += 15
            build.def_pct_bonus += 15
            build.agi_pct_bonus += 15
            score = PG_SCORE_EVENT_RISK_BONUS
            run.score += score
            return PGEventResult(True, "运气不错！三维各 +15%。", score_gained=score)
        build.atk_pct_bonus -= 5
        build.def_pct_bonus -= 5
        build.agi_pct_bonus -= 5
        return PGEventResult(True, "运气欠佳…三维各 -5%。")

    def _apply_tianren_jiaozhan(self, choice_id: str, build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "reject":
            return PGEventResult(True, "保留操作次数。")
        run.pending_affix_ops = 0
        tier_key = self.rng.choice(["peak", "supreme"])
        power_defn = self.rng.choice(SPIRIT_POWER_DEFINITIONS)
        build.spirit_power = power_defn.roll(tier_key, self.rng)
        build.spirit_tier = tier_key
        score = PG_SCORE_EVENT_RISK_BONUS
        run.score += score
        return PGEventResult(True, f"舍弃词条机会，获得极品器灵「{power_defn.name}」。", score_gained=score)

    def _apply_xinmo_shilian(self, choice_id: str, build: PGBuild, run: ProvingGroundRun, _char: Character) -> PGEventResult:
        if choice_id == "skip":
            return PGEventResult(True, "退避三舍。")
        # 心魔 = 自身构筑 × 1.2 加强版
        player = self.build_player_snapshot(build, "证道者")
        demon = CombatantSnapshot(
            name="内心魔",
            atk=int(player.atk * 1.2),
            defense=int(player.defense * 1.2),
            agility=int(player.agility * 1.2),
            max_hp=int(player.max_hp * 1.2),
            affixes=player.affixes,
            spirit_power=player.spirit_power,
        )
        battle = self.combat_service.run_battle(player, demon, scene_tags=(PG_SCENE_TAG,))
        if battle.challenger_won:
            score = PG_SCORE_EVENT_RISK_BONUS * 3
            run.score += score
            run.pending_affix_ops += 1
            return PGEventResult(True, f"心魔试炼胜利！积分 +{score}，词条操作 +1。", score_gained=score)
        return PGEventResult(True, "心魔试炼失败，但未影响本次运行。")

    def _apply_hongchen_lijie(self, _cid: str, _build: PGBuild, _run: ProvingGroundRun, character: Character) -> PGEventResult:
        character.pg_red_dust_count += 1
        count = character.pg_red_dust_count
        if count >= PG_RED_DUST_THRESHOLD:
            return PGEventResult(True, f"红尘历劫第 {count} 次……九世红尘，道心圆满。")
        return PGEventResult(True, f"红尘历劫第 {count} 次……似有所悟。")

    # -----------------------------------------------------------------------
    # 生命周期 — 进入战场
    # -----------------------------------------------------------------------

    def enter_proving_ground(self, character: Character) -> PGEnterResult:
        """创建新的证道战场运行。"""
        # 前置检查
        if character.is_retreating:
            return PGEnterResult(False, "闭关中，需先出关。")
        if character.is_traveling:
            return PGEnterResult(False, "游历中，需先归来。")

        # 境界检查：仅渡劫圆满可进入
        if not (character.realm_key == "dujie" and character.stage_key == "perfect"):
            return PGEnterResult(False, "仅渡劫圆满境界方可踏入证道战场。")

        # 气机检查
        if character.current_qi < PG_ENTRY_QI_COST:
            return PGEnterResult(False, f"气机不足（需要 {PG_ENTRY_QI_COST}，当前 {character.current_qi}）。")

        # 扣除气机
        character.current_qi -= PG_ENTRY_QI_COST

        # 生成地图
        pg_map = self.generate_map()

        # 选择 BOSS 类型
        boss_type = self.pick_boss_type(character)

        # 创建初始构筑（应用永久投资加成）
        build = self.create_initial_build(character)

        # 创建 run 记录
        now = now_shanghai()
        run = ProvingGroundRun(
            character_id=character.id,
            status=PG_STATUS_RUNNING,
            map_json=self.serialize_map(pg_map),
            current_node_id=0,  # 起点
            build_json=self.serialize_build(build),
            pending_affix_ops=0,
            pending_spirit_ops=0,
            boss_type=boss_type,
            boss_snapshot_json="{}",  # BOSS 快照稍后设置
            score=0,
            lingshi_invested=0,
            last_action_at=now,
        )

        return PGEnterResult(
            success=True,
            message="踏入证道战场。",
            run=run,
            pg_map=pg_map,
        )

    # -----------------------------------------------------------------------
    # 生命周期 — 推进节点
    # -----------------------------------------------------------------------

    def advance_to_node(
        self,
        run: ProvingGroundRun,
        target_node_id: int,
        character: Character,
        player_name: str,
        *,
        boss_snapshot: CombatantSnapshot | None = None,
    ) -> PGNodeResult:
        """推进到指定节点，执行该节点逻辑。"""
        if run.status != PG_STATUS_RUNNING:
            return PGNodeResult(False, "当前运行已结束。")

        if self.is_run_expired(run):
            run.status = PG_STATUS_EXPIRED
            return PGNodeResult(False, "运行已超时，本次证道作废。", run_ended=True, run_status=PG_STATUS_EXPIRED)

        pg_map = self.deserialize_map(run.map_json)
        build = self.deserialize_build(run.build_json)

        # 验证目标节点可达
        current = pg_map.get_node(run.current_node_id)
        if current is None:
            return PGNodeResult(False, "当前节点异常。")
        if target_node_id not in current.connections:
            return PGNodeResult(False, "目标节点不可达。")

        target = pg_map.get_node(target_node_id)
        if target is None:
            return PGNodeResult(False, "目标节点不存在。")

        # 根据节点类型执行逻辑
        if target.node_type in (PG_NODE_TYPE_NORMAL, PG_NODE_TYPE_ELITE, PG_NODE_TYPE_BOSS):
            result = self.run_node_combat(
                target, build, player_name, run,
                boss_snapshot=boss_snapshot,
            )
        elif target.node_type == PG_NODE_TYPE_EVENT:
            event = self.generate_event(build)
            result = PGNodeResult(
                success=True,
                message=f"遭遇事件：{event.name}",
                node_type=PG_NODE_TYPE_EVENT,
                event=event,
            )
        else:
            result = PGNodeResult(False, f"未知节点类型：{target.node_type}")

        # 更新 run 状态
        run.current_node_id = target_node_id
        run.build_json = self.serialize_build(build)
        run.last_action_at = now_shanghai()

        if result.run_ended:
            run.status = result.run_status or PG_STATUS_FAILED

        return result

    # -----------------------------------------------------------------------
    # 生命周期 — 结算
    # -----------------------------------------------------------------------

    def settle_run(self, run: ProvingGroundRun, character: Character) -> PGSettlement:
        """结算一次运行，更新角色统计。"""
        victory = run.status == PG_STATUS_COMPLETED
        boss_killed = victory

        # 更新角色统计
        character.pg_total_score += run.score
        if run.score > character.pg_best_score:
            character.pg_best_score = run.score

        # 红尘历劫荣誉: 累计 9 次自动授予
        honor_gained: str | None = None
        if character.pg_red_dust_count >= 9:
            if character.add_honor_tag("九世红尘"):
                honor_gained = "九世红尘"

        dao_traces = 0
        if victory:
            character.pg_completions += 1
            dao_traces = PG_DAO_TRACE_REWARD_PER_BOSS
            character.dao_traces += dao_traces
            # 记录 BOSS 击败
            if run.boss_type:
                character.add_pg_boss_kill(run.boss_type)

        return PGSettlement(
            victory=victory,
            total_score=run.score,
            dao_traces_gained=dao_traces,
            boss_type=run.boss_type,
            boss_killed=boss_killed,
            honor_gained=honor_gained,
        )

    # -----------------------------------------------------------------------
    # 恢复进行中运行
    # -----------------------------------------------------------------------

    @staticmethod
    def get_run_summary(run: ProvingGroundRun) -> dict:
        """获取运行摘要，用于恢复界面展示。"""
        try:
            pg_map = PGMap.from_dict(json.loads(run.map_json))
        except (json.JSONDecodeError, KeyError):
            pg_map = None
        try:
            build = PGBuild.from_dict(json.loads(run.build_json))
        except (json.JSONDecodeError, KeyError):
            build = None

        current_node = pg_map.get_node(run.current_node_id) if pg_map else None
        return {
            "status": run.status,
            "score": run.score,
            "current_layer": current_node.layer if current_node else 0,
            "total_layers": PG_MAP_LAYERS + 1,
            "affix_count": len(build.affixes) if build else 0,
            "has_spirit": build.spirit_power is not None if build else False,
            "pending_affix_ops": run.pending_affix_ops,
            "pending_spirit_ops": run.pending_spirit_ops,
            "boss_type": run.boss_type,
        }
