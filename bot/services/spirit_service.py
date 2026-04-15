from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import random
import re

from bot.data.spirits import (
    SPIRIT_NAMES,
    SPIRIT_POWER_DEFINITIONS,
    SPIRIT_TIER_DEFINITIONS,
    SPIRIT_TIER_BY_KEY,
    SpiritInstance,
    SpiritPowerEntry,
    SpiritStatEntry,
    get_spirit_power_definition,
)
from bot.models.artifact import Artifact
from bot.utils.time_utils import ensure_shanghai, now_shanghai

SPIRIT_UNLOCK_LEVEL = 30
SPIRIT_NURTURE_COST = 80
SPIRIT_REFORGE_COST = 60
SPIRIT_NURTURE_MINUTES = 60
SPIRIT_REFORGE_MINUTES = 30
SPIRIT_STATS = ("atk", "def", "agi")


@dataclass(frozen=True, slots=True)
class SpiritStatView:
    label: str
    kind_name: str
    value_text: str
    effective_bonus: int


@dataclass(frozen=True, slots=True)
class SpiritView:
    name: str
    tier_key: str
    tier_name: str
    power_name: str
    power_description: str
    stats: tuple[SpiritStatView, ...]


@dataclass(frozen=True, slots=True)
class SpiritPanelState:
    unlocked: bool
    current_spirit: SpiritView | None
    pending_spirit: SpiritView | None
    action_text: str
    process_mode: str | None
    remaining_seconds: int
    can_start_nurture: bool
    can_start_reforge: bool
    can_collect: bool
    can_accept_pending: bool
    can_discard_pending: bool
    can_rename: bool


@dataclass(slots=True)
class SpiritProcessResult:
    success: bool
    message: str
    soul_before: int
    soul_after: int
    finish_at: datetime | None = None


@dataclass(slots=True)
class SpiritCollectResult:
    success: bool
    message: str
    collected_spirit: SpiritInstance | None = None
    pending_spirit: SpiritInstance | None = None


@dataclass(slots=True)
class SpiritPendingResult:
    success: bool
    message: str
    spirit: SpiritInstance | None = None


@dataclass(slots=True)
class RenameSpiritResult:
    success: bool
    message: str
    name_before: str
    name_after: str


class SpiritService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def create_initial_name(self) -> str:
        return self.rng.choice(SPIRIT_NAMES)

    def is_unlocked(self, artifact: Artifact) -> bool:
        return (artifact.reinforce_level or 0) >= SPIRIT_UNLOCK_LEVEL

    def nurture_cost(self) -> int:
        return SPIRIT_NURTURE_COST

    def reforge_cost(self) -> int:
        return SPIRIT_REFORGE_COST

    def ensure_compatibility(self, artifact: Artifact) -> None:
        spirit = self.get_current_spirit(artifact)
        if spirit is not None and not artifact.spirit_name:
            artifact.spirit_name = self.create_initial_name()

    def get_current_spirit(self, artifact: Artifact) -> SpiritInstance | None:
        return self._load_spirit(artifact.spirit_json)

    def get_pending_spirit(self, artifact: Artifact) -> SpiritInstance | None:
        return self._load_spirit(artifact.spirit_pending_json)

    def has_pending(self, artifact: Artifact) -> bool:
        return self.get_pending_spirit(artifact) is not None

    def build_panel_state(self, artifact: Artifact, *, now: datetime | None = None) -> SpiritPanelState:
        self.ensure_compatibility(artifact)
        current = self.get_current_spirit(artifact)
        pending = self.get_pending_spirit(artifact)
        unlocked = self.is_unlocked(artifact)
        current_time = ensure_shanghai(now or now_shanghai())
        remaining_seconds = self.remaining_seconds(artifact, now=current_time)
        can_collect = artifact.spirit_refining_until is not None and remaining_seconds <= 0
        can_start_nurture = unlocked and current is None and artifact.spirit_refining_until is None and pending is None
        can_start_reforge = unlocked and current is not None and artifact.spirit_refining_until is None and pending is None
        can_accept_pending = pending is not None
        can_discard_pending = pending is not None
        can_rename = current is not None

        if not unlocked:
            action_text = f"本命法宝达到 +{SPIRIT_UNLOCK_LEVEL} 后，方可孕育器灵。"
        elif current is None and artifact.spirit_refining_until is None:
            action_text = "尚未孕育器灵，可投入器魂开始孕育。"
        elif artifact.spirit_refining_until is not None and can_collect:
            action_text = "炉火已定，可收取本次器灵结果。"
        elif artifact.spirit_refining_until is not None:
            label = "器灵孕育中" if artifact.spirit_refining_mode == "nurture" else "器灵重炼中"
            action_text = f"{label}，尚余 {self._format_remaining(remaining_seconds)}。"
        elif pending is not None:
            action_text = "新器灵结果已出，请决定是否纳灵。"
        else:
            action_text = "器灵已安于本命法宝之中，可继续重炼追寻更高资质。"

        return SpiritPanelState(
            unlocked=unlocked,
            current_spirit=self._build_spirit_view(artifact, current) if current is not None else None,
            pending_spirit=self._build_spirit_view(artifact, pending) if pending is not None else None,
            action_text=action_text,
            process_mode=artifact.spirit_refining_mode,
            remaining_seconds=max(0, remaining_seconds),
            can_start_nurture=can_start_nurture,
            can_start_reforge=can_start_reforge,
            can_collect=can_collect,
            can_accept_pending=can_accept_pending,
            can_discard_pending=can_discard_pending,
            can_rename=can_rename,
        )

    def spirit_summary(self, artifact: Artifact) -> tuple[str, str, str]:
        self.ensure_compatibility(artifact)
        spirit = self.get_current_spirit(artifact)
        if spirit is None:
            return ("未孕器灵", "", "")
        tier_name = SPIRIT_TIER_BY_KEY[spirit.tier].name
        power_name = get_spirit_power_definition(spirit.power.power_id).name
        return (artifact.spirit_name or "无名器灵", tier_name, power_name)

    def effective_artifact_bonuses(self, artifact: Artifact) -> tuple[int, int, int]:
        self.ensure_compatibility(artifact)
        spirit = self.get_current_spirit(artifact)
        base_atk = artifact.atk_bonus or 0
        base_def = artifact.def_bonus or 0
        base_agi = artifact.agi_bonus or 0
        if spirit is None:
            return (base_atk, base_def, base_agi)
        bonuses = {
            "atk": base_atk,
            "def": base_def,
            "agi": base_agi,
        }
        for entry in spirit.stats:
            base_value = bonuses[entry.stat]
            spirit_bonus = entry.value if entry.kind == "flat" else (base_value * entry.value // 100)
            bonuses[entry.stat] += spirit_bonus
        return (bonuses["atk"], bonuses["def"], bonuses["agi"])

    def artifact_power(self, artifact: Artifact) -> int:
        atk, defense, agility = self.effective_artifact_bonuses(artifact)
        return atk + defense + agility

    def current_spirit_power(self, artifact: Artifact) -> SpiritPowerEntry | None:
        spirit = self.get_current_spirit(artifact)
        return spirit.power if spirit is not None else None

    def start_nurture(self, artifact: Artifact, *, now: datetime | None = None) -> SpiritProcessResult:
        if not self.is_unlocked(artifact):
            return SpiritProcessResult(False, f"本命法宝达到 +{SPIRIT_UNLOCK_LEVEL} 后，方可孕育器灵。", artifact.soul_shards, artifact.soul_shards)
        if self.get_current_spirit(artifact) is not None:
            return SpiritProcessResult(False, "器灵已在身侧，无需再次初孕。", artifact.soul_shards, artifact.soul_shards)
        if artifact.spirit_refining_until is not None or self.has_pending(artifact):
            return SpiritProcessResult(False, "当前已有器灵流程未完，不可再启新炉。", artifact.soul_shards, artifact.soul_shards)
        soul_before = artifact.soul_shards
        if soul_before < SPIRIT_NURTURE_COST:
            return SpiritProcessResult(False, "器魂不足，尚不能孕育器灵。", soul_before, soul_before)
        artifact.soul_shards -= SPIRIT_NURTURE_COST
        finish_at = ensure_shanghai(now or now_shanghai()) + timedelta(minutes=SPIRIT_NURTURE_MINUTES)
        artifact.spirit_refining_until = finish_at
        artifact.spirit_refining_mode = "nurture"
        return SpiritProcessResult(True, "你以器魂温养灵胚，器灵正在孕育之中。", soul_before, artifact.soul_shards, finish_at)

    def start_reforge(self, artifact: Artifact, *, now: datetime | None = None) -> SpiritProcessResult:
        if not self.is_unlocked(artifact):
            return SpiritProcessResult(False, f"本命法宝达到 +{SPIRIT_UNLOCK_LEVEL} 后，方可重炼器灵。", artifact.soul_shards, artifact.soul_shards)
        if self.get_current_spirit(artifact) is None:
            return SpiritProcessResult(False, "当前尚无器灵，需先完成初次孕育。", artifact.soul_shards, artifact.soul_shards)
        if artifact.spirit_refining_until is not None or self.has_pending(artifact):
            return SpiritProcessResult(False, "当前已有器灵流程未完，不可再启重炼。", artifact.soul_shards, artifact.soul_shards)
        soul_before = artifact.soul_shards
        if soul_before < SPIRIT_REFORGE_COST:
            return SpiritProcessResult(False, "器魂不足，尚不能重炼器灵。", soul_before, soul_before)
        artifact.soul_shards -= SPIRIT_REFORGE_COST
        finish_at = ensure_shanghai(now or now_shanghai()) + timedelta(minutes=SPIRIT_REFORGE_MINUTES)
        artifact.spirit_refining_until = finish_at
        artifact.spirit_refining_mode = "reroll"
        return SpiritProcessResult(True, "你催动器火重炼器灵，新的灵相正在酝酿。", soul_before, artifact.soul_shards, finish_at)

    def collect_result(self, artifact: Artifact, *, now: datetime | None = None) -> SpiritCollectResult:
        self.ensure_compatibility(artifact)
        if artifact.spirit_refining_until is None:
            return SpiritCollectResult(False, "当前并无可收取的器灵结果。")
        current_time = ensure_shanghai(now or now_shanghai())
        if current_time < ensure_shanghai(artifact.spirit_refining_until):
            return SpiritCollectResult(False, "炉火尚未停歇，此刻还收不得器灵结果。")

        mode = artifact.spirit_refining_mode or "nurture"
        rolled = self._roll_spirit(artifact)
        artifact.spirit_refining_until = None
        artifact.spirit_refining_mode = None
        if mode == "nurture" and self.get_current_spirit(artifact) is None:
            if not artifact.spirit_name:
                artifact.spirit_name = self.create_initial_name()
            self._store_spirit(artifact, "spirit_json", rolled)
            return SpiritCollectResult(True, f"器灵「{artifact.spirit_name}」已自法宝中孕成。", collected_spirit=rolled)

        self._store_spirit(artifact, "spirit_pending_json", rolled)
        return SpiritCollectResult(True, "新的器灵结果已出，可择是否纳灵。", pending_spirit=rolled)

    def accept_pending_spirit(self, artifact: Artifact) -> SpiritPendingResult:
        self.ensure_compatibility(artifact)
        pending = self.get_pending_spirit(artifact)
        if pending is None:
            return SpiritPendingResult(False, "当前没有可纳入本命法宝的器灵结果。")
        if not artifact.spirit_name:
            artifact.spirit_name = self.create_initial_name()
        self._store_spirit(artifact, "spirit_json", pending)
        self._store_spirit(artifact, "spirit_pending_json", None)
        return SpiritPendingResult(True, f"器灵「{artifact.spirit_name}」已纳入新灵相。", pending)

    def discard_pending_spirit(self, artifact: Artifact) -> SpiritPendingResult:
        pending = self.get_pending_spirit(artifact)
        if pending is None:
            return SpiritPendingResult(False, "当前没有可放弃的器灵结果。")
        self._store_spirit(artifact, "spirit_pending_json", None)
        return SpiritPendingResult(True, "你收敛炉火，放弃了本次新灵相。", pending)

    def rename_spirit(self, artifact: Artifact, new_name: str) -> RenameSpiritResult:
        self.ensure_compatibility(artifact)
        name_before = artifact.spirit_name or ""
        if self.get_current_spirit(artifact) is None:
            return RenameSpiritResult(False, "当前尚无器灵，不可赐名。", name_before, name_before)
        cleaned = re.sub(r"\s+", "", new_name).strip()
        if artifact.spirit_rename_used:
            return RenameSpiritResult(False, "此灵之名已定，今后不可再改。", name_before, name_before)
        if len(cleaned) < 2 or len(cleaned) > 12:
            return RenameSpiritResult(False, "器灵之名需在 2 到 12 个字符之间。", name_before, name_before)
        artifact.spirit_name = cleaned
        artifact.spirit_rename_used = True
        return RenameSpiritResult(True, f"器灵自此定名为「{cleaned}」。", name_before, cleaned)

    def remaining_seconds(self, artifact: Artifact, *, now: datetime | None = None) -> int:
        if artifact.spirit_refining_until is None:
            return 0
        current_time = ensure_shanghai(now or now_shanghai())
        remaining = ensure_shanghai(artifact.spirit_refining_until) - current_time
        return int(remaining.total_seconds())

    def _roll_spirit(self, artifact: Artifact) -> SpiritInstance:
        tier = self._roll_tier()
        power_definition = self.rng.choice(SPIRIT_POWER_DEFINITIONS)
        stats = tuple(self._roll_stat_entry(artifact, stat_key, tier.key) for stat_key in SPIRIT_STATS)
        power = power_definition.roll(tier.key, self.rng)
        return SpiritInstance(tier=tier.key, stats=stats, power=power)

    def _roll_tier(self):
        return self.rng.choices(
            SPIRIT_TIER_DEFINITIONS,
            weights=[definition.weight for definition in SPIRIT_TIER_DEFINITIONS],
            k=1,
        )[0]

    def _roll_stat_entry(self, artifact: Artifact, stat_key: str, tier_key: str) -> SpiritStatEntry:
        tier = SPIRIT_TIER_BY_KEY[tier_key]
        base_value = {
            "atk": artifact.atk_bonus or 0,
            "def": artifact.def_bonus or 0,
            "agi": artifact.agi_bonus or 0,
        }[stat_key]
        if self.rng.random() < 0.5:
            pct = self.rng.randint(*tier.flat_range)
            value = max(1, base_value * pct // 100)
            return SpiritStatEntry(stat=stat_key, kind="flat", value=value)
        return SpiritStatEntry(stat=stat_key, kind="ratio", value=self.rng.randint(*tier.ratio_range))

    def _build_spirit_view(self, artifact: Artifact, spirit: SpiritInstance) -> SpiritView:
        name = artifact.spirit_name or "无名器灵"
        tier = SPIRIT_TIER_BY_KEY[spirit.tier]
        power_definition = get_spirit_power_definition(spirit.power.power_id)
        base_values = {
            "atk": artifact.atk_bonus or 0,
            "def": artifact.def_bonus or 0,
            "agi": artifact.agi_bonus or 0,
        }
        labels = {"atk": "杀伐", "def": "护体", "agi": "身法"}
        stats = []
        for entry in spirit.stats:
            effective_bonus = entry.value if entry.kind == "flat" else (base_values[entry.stat] * entry.value // 100)
            if entry.kind == "flat":
                value_text = f"+{entry.value}"
                kind_name = "固定"
            else:
                value_text = f"+{entry.value}%"
                kind_name = "倍率"
            stats.append(SpiritStatView(labels[entry.stat], kind_name, value_text, effective_bonus))
        return SpiritView(
            name=name,
            tier_key=tier.key,
            tier_name=tier.name,
            power_name=power_definition.name,
            power_description=power_definition.describe(spirit.power.rolls),
            stats=tuple(stats),
        )

    def _load_spirit(self, raw_json: str | None) -> SpiritInstance | None:
        if not raw_json:
            return None
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        tier = payload.get("tier")
        stats = payload.get("stats")
        power = payload.get("power")
        if not isinstance(tier, str) or tier not in SPIRIT_TIER_BY_KEY:
            return None
        if not isinstance(stats, list) or not isinstance(power, dict):
            return None

        stat_entries: list[SpiritStatEntry] = []
        seen_stats: set[str] = set()
        for item in stats:
            if not isinstance(item, dict):
                continue
            stat = item.get("stat")
            kind = item.get("kind")
            value = item.get("value")
            if stat not in SPIRIT_STATS or kind not in {"flat", "ratio"} or not isinstance(value, int):
                continue
            if stat in seen_stats:
                continue
            seen_stats.add(stat)
            stat_entries.append(SpiritStatEntry(stat=stat, kind=kind, value=value))
        if len(stat_entries) != 3:
            return None

        power_id = power.get("power_id")
        rolls = power.get("rolls")
        if not isinstance(power_id, str) or power_id not in {definition.power_id for definition in SPIRIT_POWER_DEFINITIONS}:
            return None
        if not isinstance(rolls, dict):
            return None
        parsed_rolls = {str(key): int(value) for key, value in rolls.items() if isinstance(value, int)}
        return SpiritInstance(tier=tier, stats=tuple(sorted(stat_entries, key=lambda item: SPIRIT_STATS.index(item.stat))), power=SpiritPowerEntry(power_id=power_id, rolls=parsed_rolls))

    def _store_spirit(self, artifact: Artifact, field_name: str, spirit: SpiritInstance | None) -> None:
        serialized = "" if spirit is None else json.dumps(spirit.to_payload(), ensure_ascii=False, separators=(",", ":"))
        setattr(artifact, field_name, serialized)

    @staticmethod
    def _format_remaining(remaining_seconds: int) -> str:
        remaining_seconds = max(0, remaining_seconds)
        minutes = remaining_seconds // 60
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}时{minutes}分"
        return f"{minutes}分"
