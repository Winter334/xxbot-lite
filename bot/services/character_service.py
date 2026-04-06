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
from bot.services.idle_service import IdleSettlement
from bot.utils.time_utils import now_shanghai, today_shanghai


TITLE_BONUS_MAP = {
    "独断万古": 0.025,
    "万战称尊": 0.02,
    "横压同代": 0.015,
    "凶威盖世": 0.01,
    "盖世无双": 0.015,
    "踏碎天关": 0.015,
    "本命通神": 0.012,
}


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
    artifact_atk_bonus: int
    artifact_def_bonus: int
    artifact_agi_bonus: int
    soul_shards: int
    title: str
    honor_tags: tuple[str, ...]
    reincarnation_count: int
    last_highlight_text: str
    current_ladder_rank: int
    best_ladder_rank: int
    daily_pvp_attempts_left: int
    idle_minutes: int
    is_retreating: bool


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


@dataclass(slots=True)
class RetreatActionResult:
    success: bool
    message: str
    settlement: IdleSettlement | None = None


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
            is_retreating=False,
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
            affix_slots_json="[]",
            affix_pending_json="[]",
        )
        character.ladder_record = LadderRecord(rank=initial_rank, wins=0, losses=0, streak=0)
        self.refresh_combat_power(character)
        session.add(player)
        await session.flush()

        broadcast_text = f"【初入仙途】{display_name} 踏入仙途，觉醒命格「{fate.name}」，本命法宝「{character.artifact.name}」。"
        if fate.broadcast_on_obtain:
            broadcast_text = f"【天命出世】{display_name} 初踏仙途，觉醒传说命格「{fate.name}」，本命法宝「{character.artifact.name}」。"
        return CreationResult(character, True, True, broadcast_text)

    def get_stage(self, character: Character) -> RealmStage:
        return get_stage(character.realm_key, character.stage_key)

    def get_next_stage(self, character: Character) -> RealmStage | None:
        return get_next_stage(self.get_stage(character))

    def _title_multiplier(self, character: Character) -> float:
        if character.title in TITLE_BONUS_MAP:
            return TITLE_BONUS_MAP[character.title]
        stage = self.get_stage(character)
        if character.title == f"{stage.realm_name}第一人":
            return 0.01
        return 0.0

    def calculate_total_stats(self, character: Character) -> TotalStats:
        stage = self.get_stage(character)
        artifact = character.artifact
        atk = stage.base_atk + ((artifact.atk_bonus or 0) if artifact else 0)
        defense = stage.base_def + ((artifact.def_bonus or 0) if artifact else 0)
        agility = stage.base_agi + ((artifact.agi_bonus or 0) if artifact else 0)
        atk = int(atk * self.fate_service.combat_multiplier(character.fate_key, "atk"))
        defense = int(defense * self.fate_service.combat_multiplier(character.fate_key, "def"))
        agility = int(agility * self.fate_service.combat_multiplier(character.fate_key, "agi"))
        title_multiplier = self._title_multiplier(character)
        if title_multiplier > 0:
            atk = int(atk * (1 + title_multiplier))
            defense = int(defense * (1 + title_multiplier))
            agility = int(agility * (1 + title_multiplier))
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
        if artifact is not None:
            self.artifact_service.ensure_affix_slots(artifact)
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
            artifact_atk_bonus=(artifact.atk_bonus or 0) if artifact else 0,
            artifact_def_bonus=(artifact.def_bonus or 0) if artifact else 0,
            artifact_agi_bonus=(artifact.agi_bonus or 0) if artifact else 0,
            soul_shards=(artifact.soul_shards or 0) if artifact else 0,
            title=title or character.title,
            honor_tags=honor_tags,
            reincarnation_count=character.reincarnation_count,
            last_highlight_text=character.last_highlight_text,
            current_ladder_rank=character.current_ladder_rank,
            best_ladder_rank=character.best_ladder_rank,
            daily_pvp_attempts_left=max(0, 5 - character.daily_pvp_attempts_used),
            idle_minutes=idle_minutes,
            is_retreating=character.is_retreating,
        )

    def can_reincarnate_today(self, character: Character) -> bool:
        return character.last_reincarnated_on != today_shanghai()

    def start_retreat(self, character: Character) -> RetreatActionResult:
        if character.is_retreating:
            return RetreatActionResult(False, "你已在洞府闭关中，无需再次入定。")
        now = now_shanghai()
        character.is_retreating = True
        character.last_idle_at = now
        character.last_highlight_text = "方才入洞府闭关，静候灵气归体。"
        return RetreatActionResult(True, "你已封闭洞府，开始闭关修炼。")

    def stop_retreat(self, character: Character, settlement: IdleSettlement) -> RetreatActionResult:
        if not character.is_retreating:
            return RetreatActionResult(False, "你当前并未闭关，无需强行出关。", settlement)
        character.is_retreating = False
        if settlement.gained_cultivation > 0 or settlement.gained_soul > 0:
            pieces: list[str] = []
            if settlement.gained_cultivation > 0:
                pieces.append(f"修为增长 {settlement.gained_cultivation}")
            if settlement.gained_soul > 0:
                pieces.append(f"器魂凝成 {settlement.gained_soul}")
            character.last_highlight_text = f"方才出关，{'，'.join(pieces)}。"
        else:
            character.last_highlight_text = "方才出关，却觉灵气未满一周天。"
        return RetreatActionResult(True, "你已出关，此番闭关所得已尽数归体。", settlement)

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
        character.is_retreating = False
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
            self.artifact_service.reset_affixes(character.artifact)
        self.refresh_combat_power(character)

        broadcast_text = None
        if new_fate.broadcast_on_obtain:
            broadcast_text = f"【轮回惊世】{character.player.display_name} 轮回之后，再得传说命格「{new_fate.name}」。"
        return ReincarnationResult(True, "旧躯尽褪，命数已换。", character, new_fate.broadcast_on_obtain, broadcast_text)
