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

        # 渡劫圆满 → 伪仙前期: 需通关证道战场击败三种 BOSS
        if stage.realm_key == "dujie" and stage.stage_key == "perfect":
            if not character.has_all_pg_boss_kills():
                return BreakthroughResult(
                    False,
                    "需先通关证道战场，击败三种道敌（天劫化身、道心投影、心魔映射）方可证道。",
                    False,
                    None,
                )
        # 伪仙内部突破: 修为圆满即可，无通天塔要求
        elif stage.realm_key == "weixian":
            pass
        # 其他境界: 通天塔层数要求
        else:
            required_floor = stage.global_stage_index * 25
            if character.highest_floor < required_floor:
                return BreakthroughResult(False, f"需先踏破通天塔第 {required_floor} 层守关。", False, required_floor)

        reached_new_realm = next_stage.stage_index == 1
        character.realm_key = next_stage.realm_key
        character.realm_index = next_stage.realm_index
        character.stage_key = next_stage.stage_key
        character.stage_index = next_stage.stage_index
        character.cultivation = 0

        # 踏入伪仙时: 彩蛋荣誉升级替换
        if stage.realm_key == "dujie" and stage.stage_key == "perfect":
            self._upgrade_honors_on_weixian(character)

        character.last_highlight_text = f"一念贯通，已入 {next_stage.display_name}。"
        self.character_service.refresh_combat_power(character)
        return BreakthroughResult(True, f"灵台震荡，你已踏入 {next_stage.display_name}。", reached_new_realm, None)

    @staticmethod
    def _upgrade_honors_on_weixian(character: Character) -> None:
        """突破伪仙时，将特定彩蛋荣誉替换为升级版本。"""
        _HONOR_UPGRADES = {
            "九世红尘": "红尘证道",
            "天心印记": "天心铸道",
            "鸿运当头": "气吞万象",
        }
        tags = list(character.stored_honor_tags())
        changed = False
        for i, tag in enumerate(tags):
            if tag in _HONOR_UPGRADES:
                tags[i] = _HONOR_UPGRADES[tag]
                changed = True
        if changed:
            character.set_honor_tags(tags)
