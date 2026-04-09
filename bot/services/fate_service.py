from __future__ import annotations

import math
import random

from bot.data.fates import FATE_DEFINITIONS, FATES_BY_KEY, RARITY_WEIGHTS, FateDefinition


class FateService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def get_fate(self, fate_key: str) -> FateDefinition:
        return FATES_BY_KEY[fate_key]

    def has_fate(self, fate_key: str) -> bool:
        return fate_key in FATES_BY_KEY

    def random_initial_luck(self) -> int:
        return self.rng.randint(1, 99)

    def roll_fate(self, *, rarity: str | None = None, exclude_key: str | None = None) -> FateDefinition:
        target_rarity = rarity
        if target_rarity is None:
            rarities = list(RARITY_WEIGHTS)
            weights = [RARITY_WEIGHTS[item] for item in rarities]
            target_rarity = self.rng.choices(rarities, weights=weights, k=1)[0]
        pool = [definition for definition in FATE_DEFINITIONS if definition.rarity == target_rarity and definition.enabled]
        if exclude_key is not None:
            filtered = [definition for definition in pool if definition.key != exclude_key]
            if filtered:
                pool = filtered
        return self.rng.choice(pool)

    def stat_multiplier(self, fate_key: str, stat_key: str) -> float:
        fate = self.get_fate(fate_key)
        value = {
            "atk": fate.atk_basis_points,
            "def": fate.def_basis_points,
            "agi": fate.agi_basis_points,
        }.get(stat_key, 0)
        return 1.0 + (value / 10_000)

    def damage_dealt_multiplier(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        return 1.0 + (fate.damage_dealt_basis_points / 10_000)

    def damage_taken_multiplier(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        return 1.0 + (fate.damage_taken_basis_points / 10_000)

    def damage_reduction_multiplier(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        return max(0.05, 1.0 - (fate.damage_reduction_basis_points / 10_000))

    def versus_higher_realm_damage_multiplier(self, fate_key: str, actor_realm_index: int, target_realm_index: int) -> float:
        fate = self.get_fate(fate_key)
        if target_realm_index <= actor_realm_index:
            return 1.0
        return 1.0 + (fate.versus_higher_realm_damage_basis_points / 10_000)

    def idle_cultivation_multiplier(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        return 1.0 + (fate.idle_cultivation_basis_points / 10_000)

    def apply_system_soul_modifier(self, fate_key: str, amount: int) -> int:
        fate = self.get_fate(fate_key)
        return self._apply_resource_modifier(amount, fate.system_soul_basis_points)

    def _apply_resource_modifier(self, amount: int, basis_points: int) -> int:
        if amount <= 0 or basis_points == 0:
            return max(0, amount)
        scaled = amount * (1 + (basis_points / 10_000))
        if basis_points > 0:
            return max(0, math.ceil(scaled))
        return max(0, math.floor(scaled))
