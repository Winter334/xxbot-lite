from __future__ import annotations

from dataclasses import dataclass
import random

from bot.data.realms import get_stage_for_floor
from bot.data.tower import BOSS_ENEMIES, NORMAL_ENEMIES
from bot.models.character import Character
from bot.services.character_service import CharacterService
from bot.services.combat_service import BattleResult, CombatService
from bot.services.fate_service import FateService


@dataclass(slots=True)
class TowerFloorResult:
    floor: int
    enemy_name: str
    victory: bool
    is_boss: bool
    reward_soul: int
    reward_cultivation: int
    bonus_drop_triggered: bool
    battle: BattleResult


@dataclass(slots=True)
class TowerRunResult:
    success: bool
    message: str
    floors: list[TowerFloorResult]
    qi_before: int
    qi_after: int
    highest_floor_before: int
    highest_floor_after: int
    total_soul: int
    total_cultivation: int


class TowerService:
    per_run_max_floors = 5
    base_bonus_drop_rate = 0.10

    def __init__(
        self,
        character_service: CharacterService,
        combat_service: CombatService,
        fate_service: FateService,
        rng: random.Random | None = None,
    ) -> None:
        self.character_service = character_service
        self.combat_service = combat_service
        self.fate_service = fate_service
        self.rng = rng or random.Random()

    def run_tower(self, character: Character, *, now=None) -> TowerRunResult:
        if character.is_retreating:
            return TowerRunResult(False, "你仍在闭关参悟中，需先出关，方可再闯通天塔。", [], character.current_qi, character.current_qi, character.highest_floor, character.highest_floor, 0, 0)
        if character.is_traveling:
            return TowerRunResult(False, "你仍在外游历，需先归来结算，方可再闯通天塔。", [], character.current_qi, character.current_qi, character.highest_floor, character.highest_floor, 0, 0)
        if character.current_qi <= 0:
            return TowerRunResult(False, "气机已尽，暂时无力再闯通天塔。", [], character.current_qi, character.current_qi, character.highest_floor, character.highest_floor, 0, 0)

        highest_floor_before = character.highest_floor
        qi_before = character.current_qi
        character.current_qi -= 1
        total_soul = 0
        total_cultivation = 0
        floors: list[TowerFloorResult] = []

        for floor in range(highest_floor_before + 1, highest_floor_before + self.per_run_max_floors + 1):
            enemy_name, enemy, is_boss = self._generate_enemy(floor)
            snapshot = self.character_service.build_snapshot(character)
            player = self.character_service.build_combatant(character, title=snapshot.title)
            scene_tags = ("scene_tower", "scene_boss") if is_boss else ("scene_tower",)
            battle = self.combat_service.run_battle(player, enemy, scene_tags=scene_tags)
            reward_soul = 0
            reward_cultivation = 0
            bonus_drop_triggered = False
            if battle.challenger_won:
                character.highest_floor = floor
                character.historical_highest_floor = max(character.historical_highest_floor, floor)
                reward_soul = 3 if is_boss else 1
                if self._roll_bonus_drop():
                    reward_soul += 1
                    bonus_drop_triggered = True
                reward_soul = self.fate_service.apply_system_soul_modifier(character.fate_key, reward_soul)
                character.artifact.soul_shards += reward_soul
                total_soul += reward_soul
                current_stage = self.character_service.get_stage(character)
                if floor <= 50:
                    extra_ratio = 0.05 if is_boss else 0.01
                    reward_cultivation += min(int(current_stage.cultivation_max * extra_ratio), max(0, current_stage.cultivation_max - character.cultivation))
                if is_boss:
                    reward_cultivation += min(int(current_stage.cultivation_max * 0.02), max(0, current_stage.cultivation_max - character.cultivation - reward_cultivation))
                if reward_cultivation > 0:
                    character.cultivation += reward_cultivation
                    total_cultivation += reward_cultivation
            floors.append(TowerFloorResult(floor, enemy_name, battle.challenger_won, is_boss, reward_soul, reward_cultivation, bonus_drop_triggered, battle))
            if not battle.challenger_won or is_boss:
                break

        if character.highest_floor > highest_floor_before:
            character.last_highlight_text = f"方才踏上通天塔第 {character.highest_floor} 层。"
        self.character_service.refresh_combat_power(character)
        return TowerRunResult(
            True,
            "登塔有成。" if character.highest_floor > highest_floor_before else "此番登塔未能再破前关。",
            floors,
            qi_before,
            character.current_qi,
            highest_floor_before,
            character.highest_floor,
            total_soul,
            total_cultivation,
        )

    def _generate_enemy(self, floor: int):
        stage = get_stage_for_floor(floor)
        band_progress = ((floor - 1) % 25) / 24 if floor > 1 else 0
        multiplier = 0.85 + (0.30 * band_progress)
        is_boss = floor % 5 == 0
        if is_boss:
            multiplier *= 1.10
        name = self.rng.choice(BOSS_ENEMIES if is_boss else NORMAL_ENEMIES)
        enemy = self.combat_service.create_combatant(
            name=f"{name}·{floor}层",
            atk=max(1, int(stage.base_atk * multiplier)),
            defense=max(1, int(stage.base_def * multiplier)),
            agility=max(1, int(stage.base_agi * multiplier)),
        )
        return name, enemy, is_boss

    def _roll_bonus_drop(self) -> bool:
        return self.rng.random() < self.base_bonus_drop_rate
