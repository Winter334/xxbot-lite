from __future__ import annotations

from dataclasses import dataclass
import json
import random
import re

from bot.data.artifact_affixes import (
    ARTIFACT_AFFIX_DEFINITIONS,
    ArtifactAffixEntry,
    get_artifact_affix_definition,
)
from bot.data.artifacts import ARTIFACT_NAMES
from bot.data.realms import RealmStage
from bot.models.artifact import Artifact


MAX_AFFIX_SLOTS = 5
AFFIX_SLOT_UNLOCK_LEVELS = (10, 20, 30, 40, 50)
AFFIX_REFINE_COST = 2
ARTIFACT_AFFIX_IDS = {definition.affix_id for definition in ARTIFACT_AFFIX_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class ArtifactAffixView:
    slot: int
    unlocked: bool
    unlock_level: int
    name: str
    description: str | None = None
    affix_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactPanelState:
    unlocked_slots: int
    current_slots: tuple[ArtifactAffixView, ...]
    pending_slots: tuple[ArtifactAffixView, ...]
    has_pending: bool


@dataclass(slots=True)
class ReinforceResult:
    success: bool
    message: str
    level_before: int
    level_after: int
    soul_cost: int
    success_rate: float
    gained_atk: int = 0
    gained_def: int = 0
    gained_agi: int = 0
    newly_unlocked_slots: tuple[int, ...] = ()


@dataclass(slots=True)
class RenameArtifactResult:
    success: bool
    message: str
    name_before: str
    name_after: str


@dataclass(slots=True)
class RefineAffixResult:
    success: bool
    message: str
    slot: int
    soul_cost: int
    soul_before: int
    soul_after: int
    pending_entry: ArtifactAffixEntry | None = None


@dataclass(slots=True)
class SavePendingAffixesResult:
    success: bool
    message: str
    applied_slots: tuple[int, ...]


@dataclass(slots=True)
class DiscardPendingAffixResult:
    success: bool
    message: str
    slot: int
    discarded_entry: ArtifactAffixEntry | None = None


class ArtifactService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def create_initial_name(self) -> str:
        return self.rng.choice(ARTIFACT_NAMES)

    def artifact_power(self, artifact: Artifact) -> int:
        return (artifact.atk_bonus or 0) + (artifact.def_bonus or 0) + (artifact.agi_bonus or 0)

    def refine_cost(self) -> int:
        return AFFIX_REFINE_COST

    def reinforce_cost(self, next_level: int) -> int:
        return 1 + ((next_level - 1) // 10)

    def reinforce_success_rate(self, next_level: int) -> float:
        if next_level <= 10:
            return 1.0
        if next_level <= 20:
            return 0.8
        if next_level <= 30:
            return 0.6
        if next_level <= 40:
            return 0.5
        return 0.25

    def unlocked_slots(self, artifact: Artifact) -> int:
        return sum(1 for unlock_level in AFFIX_SLOT_UNLOCK_LEVELS if artifact.reinforce_level >= unlock_level)

    def slot_unlock_level(self, slot: int) -> int:
        if slot < 1 or slot > MAX_AFFIX_SLOTS:
            raise ValueError(f"invalid affix slot: {slot}")
        return AFFIX_SLOT_UNLOCK_LEVELS[slot - 1]

    def slot_is_unlocked(self, artifact: Artifact, slot: int) -> bool:
        return artifact.reinforce_level >= self.slot_unlock_level(slot)

    def build_panel_state(self, artifact: Artifact) -> ArtifactPanelState:
        self.ensure_affix_slots(artifact)
        current_map = {entry.slot: entry for entry in self.get_affix_slots(artifact)}
        pending_map = {entry.slot: entry for entry in self.get_pending_affixes(artifact)}
        unlocked_slots = self.unlocked_slots(artifact)

        current_slots = tuple(self._build_current_slot_view(slot, unlocked_slots, current_map.get(slot)) for slot in range(1, MAX_AFFIX_SLOTS + 1))
        pending_slots = tuple(self._build_pending_slot_view(slot, unlocked_slots, pending_map.get(slot)) for slot in range(1, MAX_AFFIX_SLOTS + 1))
        return ArtifactPanelState(
            unlocked_slots=unlocked_slots,
            current_slots=current_slots,
            pending_slots=pending_slots,
            has_pending=bool(pending_map),
        )

    def get_active_affixes(self, artifact: Artifact) -> tuple[ArtifactAffixEntry, ...]:
        self.ensure_affix_slots(artifact)
        return tuple(self.get_affix_slots(artifact))

    def get_affix_slots(self, artifact: Artifact) -> list[ArtifactAffixEntry]:
        return self._load_entries(artifact.affix_slots_json)

    def get_pending_affixes(self, artifact: Artifact) -> list[ArtifactAffixEntry]:
        return self._load_entries(artifact.affix_pending_json)

    def ensure_affix_slots(self, artifact: Artifact) -> tuple[int, ...]:
        unlocked_slots = self.unlocked_slots(artifact)
        current_map = {entry.slot: entry for entry in self.get_affix_slots(artifact) if 1 <= entry.slot <= unlocked_slots}
        pending_map = {entry.slot: entry for entry in self.get_pending_affixes(artifact) if 1 <= entry.slot <= unlocked_slots}

        newly_unlocked: list[int] = []
        for slot in range(1, unlocked_slots + 1):
            if slot not in current_map:
                current_map[slot] = self._roll_affix(slot)
                newly_unlocked.append(slot)

        self._store_entries(artifact, "affix_slots_json", current_map.values())
        self._store_entries(artifact, "affix_pending_json", pending_map.values())
        return tuple(newly_unlocked)

    def refine_affix(self, artifact: Artifact, slot: int, rng: random.Random | None = None) -> RefineAffixResult:
        self.ensure_affix_slots(artifact)
        if slot < 1 or slot > MAX_AFFIX_SLOTS:
            return RefineAffixResult(False, "所选词条槽位不存在。", slot, 0, artifact.soul_shards, artifact.soul_shards)
        if not self.slot_is_unlocked(artifact, slot):
            return RefineAffixResult(
                False,
                f"槽{slot} 尚未解锁，需要本命法宝达到 +{self.slot_unlock_level(slot)}。",
                slot,
                0,
                artifact.soul_shards,
                artifact.soul_shards,
            )

        soul_before = artifact.soul_shards
        soul_cost = self.refine_cost()
        if soul_before < soul_cost:
            return RefineAffixResult(False, "器魂不足，尚不能洗炼此槽。", slot, soul_cost, soul_before, soul_before)

        artifact.soul_shards -= soul_cost
        roller = rng or self.rng
        pending_map = {entry.slot: entry for entry in self.get_pending_affixes(artifact)}
        pending_entry = self._roll_affix(slot, roller)
        pending_map[slot] = pending_entry
        self._store_entries(artifact, "affix_pending_json", pending_map.values())
        definition = get_artifact_affix_definition(pending_entry.affix_id)
        return RefineAffixResult(
            True,
            f"槽{slot} 洗出待选词条「{definition.name}」。",
            slot,
            soul_cost,
            soul_before,
            artifact.soul_shards,
            pending_entry,
        )

    def save_pending_affixes(self, artifact: Artifact) -> SavePendingAffixesResult:
        self.ensure_affix_slots(artifact)
        pending_entries = self.get_pending_affixes(artifact)
        if not pending_entries:
            return SavePendingAffixesResult(False, "当前没有可保存的待选词条。", ())

        current_map = {entry.slot: entry for entry in self.get_affix_slots(artifact)}
        applied_slots = tuple(sorted(entry.slot for entry in pending_entries))
        for entry in pending_entries:
            current_map[entry.slot] = entry
        self._store_entries(artifact, "affix_slots_json", current_map.values())
        self._store_entries(artifact, "affix_pending_json", ())
        return SavePendingAffixesResult(True, f"已将槽{ '、槽'.join(str(slot) for slot in applied_slots)} 的待选词条写入本命法宝。", applied_slots)

    def discard_pending_affix(self, artifact: Artifact, slot: int) -> DiscardPendingAffixResult:
        self.ensure_affix_slots(artifact)
        if slot < 1 or slot > MAX_AFFIX_SLOTS:
            return DiscardPendingAffixResult(False, "所选词条槽位不存在。", slot)
        if not self.slot_is_unlocked(artifact, slot):
            return DiscardPendingAffixResult(
                False,
                f"槽{slot} 尚未解锁，需要本命法宝达到 +{self.slot_unlock_level(slot)}。",
                slot,
            )

        pending_map = {entry.slot: entry for entry in self.get_pending_affixes(artifact)}
        discarded_entry = pending_map.pop(slot, None)
        if discarded_entry is None:
            return DiscardPendingAffixResult(False, f"槽{slot} 当前没有可放弃的待选词条。", slot)

        self._store_entries(artifact, "affix_pending_json", pending_map.values())
        definition = get_artifact_affix_definition(discarded_entry.affix_id)
        return DiscardPendingAffixResult(
            True,
            f"已放弃槽{slot} 的待选词条「{definition.name}」。",
            slot,
            discarded_entry,
        )

    def reset_affixes(self, artifact: Artifact) -> None:
        artifact.affix_slots_json = "[]"
        artifact.affix_pending_json = "[]"

    def describe_affix(self, entry: ArtifactAffixEntry) -> str:
        return get_artifact_affix_definition(entry.affix_id).describe(entry.rolls)

    def affix_name(self, entry: ArtifactAffixEntry) -> str:
        return get_artifact_affix_definition(entry.affix_id).name

    def _growth_total(self, stage: RealmStage) -> int:
        baseline = stage.base_atk + stage.base_def + stage.base_agi
        return max(1, round(baseline * 0.18 / stage.reinforce_cap))

    def reinforce(self, artifact: Artifact, stage: RealmStage, rng: random.Random | None = None) -> ReinforceResult:
        roller = rng or self.rng
        level_before = artifact.reinforce_level
        next_level = level_before + 1
        if level_before >= stage.reinforce_cap:
            return ReinforceResult(False, f"当前境界最多只能将本命法宝强化到 +{stage.reinforce_cap}。", level_before, level_before, 0, 0.0)

        soul_cost = self.reinforce_cost(next_level)
        if artifact.soul_shards < soul_cost:
            return ReinforceResult(False, "器魂不足，尚不能再锻本命法宝。", level_before, level_before, soul_cost, self.reinforce_success_rate(next_level))

        artifact.soul_shards -= soul_cost
        success_rate = self.reinforce_success_rate(next_level)
        if roller.random() > success_rate:
            return ReinforceResult(
                False,
                f"器魂散去，{artifact.name} 未能踏入 +{next_level}，但法宝并未受损。",
                level_before,
                level_before,
                soul_cost,
                success_rate,
            )

        growth_total = self._growth_total(stage)
        growth = [0, 0, 0]
        for _ in range(growth_total):
            growth[roller.randint(0, 2)] += 1

        artifact.reinforce_level = next_level
        artifact.atk_bonus += growth[0]
        artifact.def_bonus += growth[1]
        artifact.agi_bonus += growth[2]
        newly_unlocked_slots = self.ensure_affix_slots(artifact)
        return ReinforceResult(
            True,
            f"{artifact.name} 炉火一振，成功踏入 +{next_level}。",
            level_before,
            next_level,
            soul_cost,
            success_rate,
            growth[0],
            growth[1],
            growth[2],
            newly_unlocked_slots,
        )

    def rename_artifact(self, artifact: Artifact, new_name: str) -> RenameArtifactResult:
        name_before = artifact.name
        cleaned = re.sub(r"\s+", "", new_name).strip()
        if artifact.artifact_rename_used:
            return RenameArtifactResult(False, "此生本命改名机缘已尽。", name_before, name_before)
        if len(cleaned) < 2 or len(cleaned) > 12:
            return RenameArtifactResult(False, "本命法宝之名需在 2 到 12 个字符之间。", name_before, name_before)
        artifact.name = cleaned
        artifact.artifact_rename_used = True
        return RenameArtifactResult(True, f"本命法宝自此更名为「{cleaned}」。", name_before, cleaned)

    def _roll_affix(self, slot: int, rng: random.Random | None = None) -> ArtifactAffixEntry:
        roller = rng or self.rng
        definition = roller.choice(ARTIFACT_AFFIX_DEFINITIONS)
        return ArtifactAffixEntry(slot=slot, affix_id=definition.affix_id, rolls=definition.roll(roller))

    def _build_current_slot_view(self, slot: int, unlocked_slots: int, entry: ArtifactAffixEntry | None) -> ArtifactAffixView:
        unlock_level = self.slot_unlock_level(slot)
        if slot > unlocked_slots:
            return ArtifactAffixView(slot, False, unlock_level, f"未解锁（+{unlock_level}）")
        if entry is None:
            return ArtifactAffixView(slot, True, unlock_level, "—")
        return ArtifactAffixView(
            slot,
            True,
            unlock_level,
            self.affix_name(entry),
            self.describe_affix(entry),
            entry.affix_id,
        )

    def _build_pending_slot_view(self, slot: int, unlocked_slots: int, entry: ArtifactAffixEntry | None) -> ArtifactAffixView:
        unlock_level = self.slot_unlock_level(slot)
        if slot > unlocked_slots or entry is None:
            return ArtifactAffixView(slot, slot <= unlocked_slots, unlock_level, "—")
        return ArtifactAffixView(
            slot,
            True,
            unlock_level,
            self.affix_name(entry),
            self.describe_affix(entry),
            entry.affix_id,
        )

    def _load_entries(self, raw_json: str | None) -> list[ArtifactAffixEntry]:
        if not raw_json:
            return []
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []

        entries: dict[int, ArtifactAffixEntry] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            slot = item.get("slot")
            affix_id = item.get("affix_id")
            rolls = item.get("rolls")
            if not isinstance(slot, int) or not isinstance(affix_id, str) or affix_id not in ARTIFACT_AFFIX_IDS:
                continue
            if not isinstance(rolls, dict):
                continue
            parsed_rolls = {str(key): int(value) for key, value in rolls.items() if isinstance(value, int)}
            entries[slot] = ArtifactAffixEntry(slot=slot, affix_id=affix_id, rolls=parsed_rolls)
        return [entries[slot] for slot in sorted(entries)]

    def _store_entries(self, artifact: Artifact, field_name: str, entries) -> None:
        serialized = [entry.to_payload() for entry in sorted(entries, key=lambda item: item.slot)]
        setattr(artifact, field_name, json.dumps(serialized, ensure_ascii=False, separators=(",", ":")))
