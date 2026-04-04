from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.character import Character
from bot.services.artifact_service import ArtifactService
from bot.services.character_service import CharacterService


@dataclass(slots=True)
class LeaderboardEntry:
    rank: int
    player_name: str
    primary_text: str
    secondary_text: str


@dataclass(slots=True)
class LeaderboardResult:
    category: str
    title: str
    subtitle: str
    entries: list[LeaderboardEntry]


class RankingService:
    def __init__(self, character_service: CharacterService, artifact_service: ArtifactService) -> None:
        self.character_service = character_service
        self.artifact_service = artifact_service

    async def build_leaderboard(
        self,
        session: AsyncSession,
        category: str,
        viewer: Character | None = None,
        limit: int = 10,
    ) -> LeaderboardResult:
        characters = await self.character_service.list_characters(session)
        if category == "power":
            ordered = sorted(characters, key=lambda char: (-self.character_service.calculate_total_stats(char).combat_power, char.id))
            return LeaderboardResult(
                category,
                "综合战力榜",
                "只看牌面，不直接决定胜负。",
                [
                    LeaderboardEntry(index, char.player.display_name, f"战力 {self.character_service.calculate_total_stats(char).combat_power}", self.character_service.get_stage(char).display_name)
                    for index, char in enumerate(ordered[:limit], start=1)
                ],
            )

        if category == "tower":
            ordered = sorted(characters, key=lambda char: (-char.historical_highest_floor, -char.highest_floor, char.id))
            return LeaderboardResult(
                category,
                "通天塔榜",
                "只看最高已踏破层数。",
                [
                    LeaderboardEntry(index, char.player.display_name, f"塔层 {char.historical_highest_floor}", self.character_service.get_stage(char).display_name)
                    for index, char in enumerate(ordered[:limit], start=1)
                ],
            )

        if category == "artifact":
            ordered = sorted(characters, key=lambda char: (-self.artifact_service.artifact_power(char.artifact), char.id))
            return LeaderboardResult(
                category,
                "本命法宝榜",
                "器魂所聚，本命分高下。",
                [
                    LeaderboardEntry(index, char.player.display_name, f"{char.artifact.name} +{char.artifact.reinforce_level}", f"总成长 {self.artifact_service.artifact_power(char.artifact)}")
                    for index, char in enumerate(ordered[:limit], start=1)
                ],
            )

        if category == "realm":
            ordered = sorted(characters, key=lambda char: (-(char.realm_index * 10 + char.stage_index), -char.cultivation, char.id))
            return LeaderboardResult(
                category,
                "境界榜",
                "境界与当前修为共同排序。",
                [
                    LeaderboardEntry(index, char.player.display_name, self.character_service.get_stage(char).display_name, f"修为 {char.cultivation}")
                    for index, char in enumerate(ordered[:limit], start=1)
                ],
            )

        if category == "realm_power" and viewer is not None:
            ordered = [char for char in characters if char.realm_key == viewer.realm_key]
            ordered.sort(key=lambda char: (-self.character_service.calculate_total_stats(char).combat_power, char.id))
            realm_name = self.character_service.get_stage(viewer).realm_name
            return LeaderboardResult(
                category,
                f"{realm_name}战力榜",
                "同境争锋，更看法宝与命格。",
                [
                    LeaderboardEntry(index, char.player.display_name, f"战力 {self.character_service.calculate_total_stats(char).combat_power}", self.character_service.get_stage(char).display_name)
                    for index, char in enumerate(ordered[:limit], start=1)
                ],
            )

        ordered = sorted(characters, key=lambda char: (char.current_ladder_rank, char.id))
        return LeaderboardResult(
            "ladder",
            "论道榜",
            "胜者取位，败者守位。",
            [
                LeaderboardEntry(index, char.player.display_name, self.character_service.get_stage(char).display_name, f"战力 {self.character_service.calculate_total_stats(char).combat_power}")
                for index, char in enumerate(ordered[:limit], start=1)
            ],
        )

    async def get_titles(self, session: AsyncSession, character: Character) -> tuple[str, tuple[str, ...]]:
        characters = await self.character_service.list_characters(session)
        title = "未立尊号"
        if character.current_ladder_rank == 1:
            title = "独断万古"
        elif 2 <= character.current_ladder_rank <= 3:
            title = "万战称尊"
        elif 4 <= character.current_ladder_rank <= 10:
            title = "横压同代"
        elif 11 <= character.current_ladder_rank <= 50:
            title = "凶威盖世"
        else:
            power_top = max(characters, key=lambda char: self.character_service.calculate_total_stats(char).combat_power, default=None)
            tower_top = max(characters, key=lambda char: char.historical_highest_floor, default=None)
            artifact_top = max(characters, key=lambda char: self.artifact_service.artifact_power(char.artifact), default=None)
            same_realm = [char for char in characters if char.realm_key == character.realm_key]
            realm_top = max(same_realm, key=lambda char: self.character_service.calculate_total_stats(char).combat_power, default=None)
            if power_top and power_top.id == character.id:
                title = "盖世无双"
            elif tower_top and tower_top.id == character.id:
                title = "踏碎天关"
            elif artifact_top and artifact_top.id == character.id:
                title = "本命通神"
            elif realm_top and realm_top.id == character.id:
                title = f"{self.character_service.get_stage(character).realm_name}第一人"

        honor_tags: list[str] = []
        if character.best_ladder_rank == 1:
            honor_tags.append("曾踏绝巅")
        elif 2 <= character.best_ladder_rank <= 3:
            honor_tags.append("曾镇一域")
        elif 4 <= character.best_ladder_rank <= 10:
            honor_tags.append("曾入天榜")
        if character.reincarnation_count > 0:
            honor_tags.append(f"轮回 {character.reincarnation_count} 次")
        return title, tuple(honor_tags[:3])
