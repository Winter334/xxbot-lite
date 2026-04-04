from __future__ import annotations

import random

from bot.data.fates import FATE_DEFINITIONS, FATES_BY_KEY, RARITY_WEIGHTS, FateDefinition


class FateService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def get_fate(self, fate_key: str) -> FateDefinition:
        return FATES_BY_KEY[fate_key]

    def roll_fate(self) -> FateDefinition:
        rarities = list(RARITY_WEIGHTS)
        weights = [RARITY_WEIGHTS[rarity] for rarity in rarities]
        rarity = self.rng.choices(rarities, weights=weights, k=1)[0]
        pool = [definition for definition in FATE_DEFINITIONS if definition.rarity == rarity and definition.enabled]
        return self.rng.choice(pool)

    def combat_multiplier(self, fate_key: str, stat_key: str) -> float:
        fate = self.get_fate(fate_key)
        if fate.category != "combat" or stat_key not in fate.affected_stats:
            return 1.0
        return 1.0 + fate.per_stat_percent

    def idle_multiplier(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        if fate.category != "cultivation":
            return 1.0
        return 1.0 + fate.percent

    def bonus_drop_rate(self, fate_key: str) -> float:
        fate = self.get_fate(fate_key)
        if fate.category != "fortune":
            return 0.0
        return fate.percent
