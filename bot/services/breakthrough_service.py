from __future__ import annotations

from dataclasses import dataclass

from bot.models.character import Character
from bot.services.character_service import CharacterService


@dataclass(slots=True)
class BreakthroughResult:
    success: bool
    message: str
    reached_new_realm: bool
    required_floor: int | None


class BreakthroughService:
    def __init__(self, character_service: CharacterService) -> None:
        self.character_service = character_service

    def attempt_breakthrough(self, character: Character) -> BreakthroughResult:
        stage = self.character_service.get_stage(character)
        next_stage = self.character_service.get_next_stage(character)
        if next_stage is None:
            return BreakthroughResult(False, "此身已至凡界尽头，暂无更高境界可破。", False, None)
        if character.cultivation < stage.cultivation_max:
            return BreakthroughResult(False, "修为尚未圆满，突破时机未到。", False, stage.global_stage_index * 25)

        required_floor = stage.global_stage_index * 25
        if character.highest_floor < required_floor:
            return BreakthroughResult(False, f"需先踏破通天塔第 {required_floor} 层守关。", False, required_floor)

        reached_new_realm = next_stage.stage_index == 1
        character.realm_key = next_stage.realm_key
        character.realm_index = next_stage.realm_index
        character.stage_key = next_stage.stage_key
        character.stage_index = next_stage.stage_index
        character.cultivation = 0
        character.last_highlight_text = f"一念贯通，已入 {next_stage.display_name}。"
        self.character_service.refresh_combat_power(character)
        return BreakthroughResult(True, f"灵台震荡，你已踏入 {next_stage.display_name}。", reached_new_realm, required_floor)
