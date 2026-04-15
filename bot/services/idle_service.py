from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import random

from bot.data.realms import get_stage, get_stage_for_floor
from bot.models.character import Character
from bot.services.fate_service import FateService
from bot.utils.time_utils import ensure_shanghai, now_shanghai


@dataclass(slots=True)
class IdleSettlement:
    gained_cultivation: int
    settled_cycles: int
    settled_minutes: int
    recovered_qi: int
    gained_soul: int
    gained_luck: int
    retreat_mode: str


class IdleService:
    idle_cycle_minutes = 10
    idle_cap_hours = 24
    qi_recover_minutes = 20
    soul_drop_chance_per_cycle = 0.25
    soul_focus_soul_per_cycle = 5
    soul_focus_cultivation_ratio = 0.1

    def __init__(self, fate_service: FateService, rng: random.Random | None = None) -> None:
        self.fate_service = fate_service
        self.rng = rng or random.Random()

    def _idle_speed_multiplier(self, current_stage) -> int:
        if current_stage.realm_index == 1:
            return 5
        if current_stage.realm_index == 2 and current_stage.stage_index <= 2:
            return 4
        if current_stage.realm_index == 2:
            return 2
        return 1

    def recover_qi(self, character: Character, *, now=None) -> int:
        current_time = ensure_shanghai(now or now_shanghai())
        return self._recover_qi(character, current_time)

    def settle_retreat(self, character: Character, *, now=None) -> IdleSettlement:
        current_time = ensure_shanghai(now or now_shanghai())
        recovered_qi = self._recover_qi(character, current_time)
        if not character.is_retreating:
            return IdleSettlement(0, 0, 0, recovered_qi, 0, 0, getattr(character, "retreat_mode", "cultivation"))
        gained_cultivation, cycles, minutes = self._settle_idle(character, current_time)
        retreat_mode = getattr(character, "retreat_mode", "cultivation")
        if retreat_mode == "soul":
            gained_soul = self._settle_soul_focus(character, cycles)
            gained_cultivation = self._trim_soul_focus_cultivation(character, gained_cultivation)
        else:
            gained_soul = self._roll_idle_soul(character, cycles)
        gained_luck = self._settle_luck(character, minutes)
        return IdleSettlement(gained_cultivation, cycles, minutes, recovered_qi, gained_soul, gained_luck, retreat_mode)

    def current_idle_minutes(self, character: Character, *, now=None) -> int:
        if not character.is_retreating:
            return 0
        current_time = ensure_shanghai(now or now_shanghai())
        elapsed = current_time - ensure_shanghai(character.last_idle_at)
        capped = min(elapsed, timedelta(hours=self.idle_cap_hours))
        return max(0, int(capped.total_seconds() // 60))

    def restart_idle_from_now(self, character: Character, *, now=None) -> None:
        character.last_idle_at = ensure_shanghai(now or now_shanghai())

    def _recover_qi(self, character: Character, current_time) -> int:
        if character.current_qi >= character.qi_max:
            character.last_qi_recovered_at = current_time
            return 0

        elapsed = current_time - ensure_shanghai(character.last_qi_recovered_at)
        recovered = int(elapsed.total_seconds() // (self.qi_recover_minutes * 60))
        recovered = min(recovered, character.qi_max - character.current_qi)
        if recovered <= 0:
            return 0

        character.current_qi += recovered
        character.last_qi_recovered_at = ensure_shanghai(character.last_qi_recovered_at) + timedelta(minutes=recovered * self.qi_recover_minutes)
        if character.current_qi >= character.qi_max:
            character.last_qi_recovered_at = current_time
        return recovered

    def _settle_idle(self, character: Character, current_time) -> tuple[int, int, int]:
        elapsed = current_time - ensure_shanghai(character.last_idle_at)
        capped = min(elapsed, timedelta(hours=self.idle_cap_hours))
        cycles = int(capped.total_seconds() // (self.idle_cycle_minutes * 60))
        if cycles <= 0:
            return 0, 0, max(0, int(capped.total_seconds() // 60))

        floor_stage = get_stage_for_floor(max(character.highest_floor, 1))
        current_stage = get_stage(character.realm_key, character.stage_key)
        per_cycle = max(1, int(floor_stage.cultivation_max * 0.01))
        multiplier = self.fate_service.idle_cultivation_multiplier(character.fate_key) * self._idle_speed_multiplier(current_stage)
        gained = int(per_cycle * cycles * multiplier)
        actual = min(gained, max(0, current_stage.cultivation_max - character.cultivation))
        character.cultivation += actual
        character.last_idle_at = current_time
        return actual, cycles, cycles * self.idle_cycle_minutes

    def _roll_idle_soul(self, character: Character, cycles: int) -> int:
        if cycles <= 0 or character.artifact is None:
            return 0
        gained = 0
        for _ in range(cycles):
            if self.rng.random() < self.soul_drop_chance_per_cycle:
                gained += 1
        gained = self.fate_service.apply_system_soul_modifier(character.fate_key, gained)
        if gained > 0:
            character.artifact.soul_shards += gained
        return gained

    def _settle_soul_focus(self, character: Character, cycles: int) -> int:
        if cycles <= 0 or character.artifact is None:
            return 0
        gained = cycles * self.soul_focus_soul_per_cycle
        gained = self.fate_service.apply_system_soul_modifier(character.fate_key, gained)
        if gained > 0:
            character.artifact.soul_shards += gained
        return gained

    def _trim_soul_focus_cultivation(self, character: Character, gained_cultivation: int) -> int:
        if gained_cultivation <= 0:
            return 0
        current_stage = get_stage(character.realm_key, character.stage_key)
        target = max(1, int(gained_cultivation * self.soul_focus_cultivation_ratio))
        actual = min(target, max(0, current_stage.cultivation_max - character.cultivation + gained_cultivation))
        character.cultivation = max(0, character.cultivation - (gained_cultivation - actual))
        return actual

    def _settle_luck(self, character: Character, settled_minutes: int) -> int:
        if settled_minutes <= 0 or character.faction != "righteous":
            return 0
        gained = settled_minutes * 30 // 60
        if gained > 0:
            character.luck += gained
        return gained
