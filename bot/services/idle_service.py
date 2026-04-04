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


class IdleService:
    idle_cycle_minutes = 10
    idle_cap_hours = 24
    qi_recover_minutes = 20
    soul_drop_chance_per_cycle = 0.04

    def __init__(self, fate_service: FateService, rng: random.Random | None = None) -> None:
        self.fate_service = fate_service
        self.rng = rng or random.Random()

    def settle(self, character: Character, *, now=None) -> IdleSettlement:
        current_time = ensure_shanghai(now or now_shanghai())
        recovered_qi = self._recover_qi(character, current_time)
        gained_cultivation, cycles, minutes = self._settle_idle(character, current_time)
        gained_soul = self._roll_idle_soul(character, cycles)
        return IdleSettlement(gained_cultivation, cycles, minutes, recovered_qi, gained_soul)

    def current_idle_minutes(self, character: Character, *, now=None) -> int:
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
        multiplier = self.fate_service.idle_multiplier(character.fate_key)
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
        if gained > 0:
            character.artifact.soul_shards += gained
        return gained
