from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.realms import RealmStage, get_next_stage, get_stage
from bot.models.artifact import Artifact
from bot.models.character import Character
from bot.models.ladder_record import LadderRecord
from bot.models.player import Player
from bot.services.artifact_service import ArtifactService
from bot.services.fate_service import FateService
from bot.utils.time_utils import now_shanghai, today_shanghai


@dataclass(slots=True)
class TotalStats:
    atk: int
    defense: int
    agility: int
    combat_power: int


@dataclass(slots=True)
class CharacterSnapshot:
    character_id: int
    player_name: str
    realm_display: str
    cultivation: int
    cultivation_max: int
    highest_floor: int
    historical_highest_floor: int
    qi_current: int
    qi_max: int
    total_atk: int
    total_def: int
    total_agi: int
    combat_power: int
    fate_name: str
    fate_rarity: str
    fate_summary: str
    artifact_name: str
    artifact_level: int
    artifact_power: int
    soul_shards: int
    title: str
    honor_tags: tuple[str, ...]
    reincarnation_count: int
    last_highlight_text: str
    current_ladder_rank: int
    best_ladder_rank: int
    daily_pvp_attempts_left: int
    idle_minutes: int


@dataclass(slots=True)
class CreationResult:
    character: Character
    created: bool
    broadcast_needed: bool
    broadcast_text: str | None


@dataclass(slots=True)
class ReincarnationResult:
    success: bool
    message: str
    character: Character
    broadcast_needed: bool
    broadcast_text: str | None


class CharacterService:
    def __init__(self, fate_service: FateService, artifact_service: ArtifactService) -> None:
        self.fate_service = fate_service
        self.artifact_service = artifact_service

    async def get_character_by_discord_id(self, session: AsyncSession, discord_user_id: int | str) -> Character | None:
        statement = (
            select(Character)
            .join(Character.player)
            .where(Player.discord_user_id == str(discord_user_id))
            .options(
                selectinload(Character.player),
                selectinload(Character.artifact),
                selectinload(Character.ladder_record),
            )
        )
        return await session.scalar(statement)

    async def get_character_by_rank(self, session: AsyncSession, rank: int) -> Character | None:
        statement = (
            select(Character)
            .where(Character.current_ladder_rank == rank)
            .options(
                selectinload(Character.player),
                selectinload(Character.artifact),
                selectinload(Character.ladder_record),
            )
        )
        return await session.scalar(statement)

    async def list_characters(self, session: AsyncSession) -> list[Character]:
        statement = select(Character).options(
            selectinload(Character.player),
            selectinload(Character.artifact),
            selectinload(Character.ladder_record),
        )
        result = await session.scalars(statement)
        return list(result.all())

    async def get_or_create_character(
        self,
        session: AsyncSession,
        discord_user_id: int | str,
        display_name: str,
    ) -> CreationResult:
        existing = await self.get_character_by_discord_id(session, discord_user_id)
        if existing is not None:
            return CreationResult(existing, False, False, None)

        now = now_shanghai()
        existing_count = await session.scalar(select(func.count(Character.id)))
        initial_rank = int(existing_count or 0) + 1
        fate = self.fate_service.roll_fate()

        player = Player(discord_user_id=str(discord_user_id), display_name=display_name)
        character = Character(
            player=player,
            realm_key="lianqi",
            realm_index=1,
            stage_key="early",
            stage_index=1,
            cultivation=0,
            highest_floor=0,
            historical_highest_floor=0,
            current_qi=6,
            qi_max=6,
            last_idle_at=now,
            last_qi_recovered_at=now,
            fate_key=fate.key,
            current_ladder_rank=initial_rank,
            best_ladder_rank=initial_rank,
            last_highlight_text=f"方入仙途，得命格「{fate.name}」。",
        )
        character.artifact = Artifact(
            name=self.artifact_service.create_initial_name(),
            artifact_rename_used=False,
            reinforce_level=0,
            atk_bonus=0,
            def_bonus=0,
            agi_bonus=0,
            soul_shards=0,
        )
        character.ladder_record = LadderRecord(rank=initial_rank, wins=0, losses=0, streak=0)
        self.refresh_combat_power(character)
        session.add(player)
        await session.flush()

        broadcast_text = None
        if fate.broadcast_on_obtain:
            broadcast_text = f"【天命出世】{display_name} 初踏仙途，竟得传说命格「{fate.name}」。"
        return CreationResult(character, True, fate.broadcast_on_obtain, broadcast_text)

    def get_stage(self, character: Character) -> RealmStage:
        return get_stage(character.realm_key, character.stage_key)

    def get_next_stage(self, character: Character) -> RealmStage | None:
        return get_next_stage(self.get_stage(character))

    def calculate_total_stats(self, character: Character) -> TotalStats:
        stage = self.get_stage(character)
        artifact = character.artifact
        atk = stage.base_atk + ((artifact.atk_bonus or 0) if artifact else 0)
        defense = stage.base_def + ((artifact.def_bonus or 0) if artifact else 0)
        agility = stage.base_agi + ((artifact.agi_bonus or 0) if artifact else 0)
        atk = int(atk * self.fate_service.combat_multiplier(character.fate_key, "atk"))
        defense = int(defense * self.fate_service.combat_multiplier(character.fate_key, "def"))
        agility = int(agility * self.fate_service.combat_multiplier(character.fate_key, "agi"))
        combat_power = int(atk * 1.35 + defense * 1.5 + agility * 1.15)
        return TotalStats(atk, defense, agility, combat_power)

    def refresh_combat_power(self, character: Character) -> int:
        stats = self.calculate_total_stats(character)
        character.combat_power = stats.combat_power
        return stats.combat_power

    def build_snapshot(
        self,
        character: Character,
        *,
        title: str | None = None,
        honor_tags: tuple[str, ...] = (),
        idle_minutes: int = 0,
    ) -> CharacterSnapshot:
        stage = self.get_stage(character)
        artifact = character.artifact
        stats = self.calculate_total_stats(character)
        fate = self.fate_service.get_fate(character.fate_key)
        return CharacterSnapshot(
            character_id=character.id,
            player_name=character.player.display_name,
            realm_display=stage.display_name,
            cultivation=character.cultivation,
            cultivation_max=stage.cultivation_max,
            highest_floor=character.highest_floor,
            historical_highest_floor=character.historical_highest_floor,
            qi_current=character.current_qi,
            qi_max=character.qi_max,
            total_atk=stats.atk,
            total_def=stats.defense,
            total_agi=stats.agility,
            combat_power=stats.combat_power,
            fate_name=fate.name,
            fate_rarity=fate.rarity,
            fate_summary=fate.effect_summary(),
            artifact_name=artifact.name if artifact else "未铸本命",
            artifact_level=(artifact.reinforce_level or 0) if artifact else 0,
            artifact_power=self.artifact_service.artifact_power(artifact) if artifact else 0,
            soul_shards=(artifact.soul_shards or 0) if artifact else 0,
            title=title or character.title,
            honor_tags=honor_tags,
            reincarnation_count=character.reincarnation_count,
            last_highlight_text=character.last_highlight_text,
            current_ladder_rank=character.current_ladder_rank,
            best_ladder_rank=character.best_ladder_rank,
            daily_pvp_attempts_left=max(0, 5 - character.daily_pvp_attempts_used),
            idle_minutes=idle_minutes,
        )

    def can_reincarnate_today(self, character: Character) -> bool:
        return character.last_reincarnated_on != today_shanghai()

    async def reincarnate(self, session: AsyncSession, character: Character) -> ReincarnationResult:
        if not self.can_reincarnate_today(character):
            return ReincarnationResult(False, "今日已轮回一次，道躯尚未重新稳固。", character, False, None)

        new_fate = self.fate_service.roll_fate()
        today = today_shanghai()
        now = now_shanghai()
        character.realm_key = "lianqi"
        character.realm_index = 1
        character.stage_key = "early"
        character.stage_index = 1
        character.cultivation = 0
        character.highest_floor = 0
        character.current_qi = character.qi_max
        character.last_idle_at = now
        character.last_qi_recovered_at = now
        character.fate_key = new_fate.key
        character.daily_pvp_attempts_used = 0
        character.last_pvp_reset_on = today
        character.reincarnation_count += 1
        character.last_reincarnated_on = today
        character.last_highlight_text = f"轮回一转，再得命格「{new_fate.name}」。"
        if character.artifact is not None:
            character.artifact.reinforce_level = 0
            character.artifact.atk_bonus = 0
            character.artifact.def_bonus = 0
            character.artifact.agi_bonus = 0
            character.artifact.soul_shards = 0
        self.refresh_combat_power(character)

        broadcast_text = None
        if new_fate.broadcast_on_obtain:
            broadcast_text = f"【轮回惊世】{character.player.display_name} 轮回之后，再得传说命格「{new_fate.name}」。"
        return ReincarnationResult(True, "旧躯尽褪，命数已换。", character, new_fate.broadcast_on_obtain, broadcast_text)
