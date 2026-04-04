from __future__ import annotations

from dataclasses import dataclass
import random

from bot.data.artifacts import ARTIFACT_NAMES
from bot.data.realms import RealmStage
from bot.models.artifact import Artifact


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


class ArtifactService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def create_initial_name(self) -> str:
        return self.rng.choice(ARTIFACT_NAMES)

    def artifact_power(self, artifact: Artifact) -> int:
        return (artifact.atk_bonus or 0) + (artifact.def_bonus or 0) + (artifact.agi_bonus or 0)

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
        )
