from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.realms import RealmStage, get_next_stage, get_stage
from bot.models.artifact import Artifact
from bot.models.character import Character
from bot.models.ladder_record import LadderRecord
from bot.models.player import Player
from bot.services.combat_service import CombatantSnapshot
from bot.services.artifact_service import ArtifactService
from bot.services.fate_service import FateService
from bot.services.idle_service import IdleSettlement
from bot.services.spirit_service import SpiritService
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
    realm_index: int
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
    spirit_name: str
    spirit_tier_name: str
    spirit_power_name: str
    sect_name: str
    sect_role: str
    sect_contribution_daily: int
    sect_last_settlement_on: date | None
    sect_last_settlement_summary: str
    lingshi: int
    soul_shards: int
    title: str
    faction_key: str
    faction_name: str
    faction_title: str
    virtue: int
    infamy: int
    luck: int
    rewrite_chances: int
    bounty_soul: int
    honor_tags: tuple[str, ...]
    reincarnation_count: int
    last_highlight_text: str
    current_ladder_rank: int
    best_ladder_rank: int
    daily_pvp_attempts_left: int
    idle_minutes: int
    is_retreating: bool
    retreat_mode: str
    is_traveling: bool
    travel_minutes: int
    travel_duration_minutes: int
    travel_selected_duration_minutes: int
    travel_atk_pct: int
    travel_def_pct: int
    travel_agi_pct: int
    pg_best_score: int
    pg_completions: int


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
class FateRewriteResult:
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
    RETREAT_MODE_NAMES = {
        "cultivation": "修炼",
        "soul": "炼魂",
    }

    def __init__(self, fate_service: FateService, artifact_service: ArtifactService, spirit_service: SpiritService) -> None:
        self.fate_service = fate_service
        self.artifact_service = artifact_service
        self.spirit_service = spirit_service

    def _ensure_character_compatibility(self, character: Character) -> None:
        if not self.fate_service.has_fate(character.fate_key):
            character.fate_key = self.fate_service.roll_fate().key
        if (character.luck or 0) <= 0:
            character.luck = self.fate_service.random_initial_luck()
        if not getattr(character, "honor_tags_json", ""):
            character.honor_tags_json = "[]"
        else:
            character.set_honor_tags(character.stored_honor_tags())
        if not character.faction:
            character.faction = "neutral"
        if getattr(character, "sect_task_state_json", None) is None:
            character.sect_task_state_json = ""
        if not getattr(character, "retreat_mode", ""):
            character.retreat_mode = "cultivation"
        if character.artifact is not None:
            self.spirit_service.ensure_compatibility(character.artifact)

    async def get_character_by_discord_id(self, session: AsyncSession, discord_user_id: int | str) -> Character | None:
        statement = (
            select(Character)
            .join(Character.player)
            .where(Player.discord_user_id == str(discord_user_id))
            .options(
                selectinload(Character.player),
                selectinload(Character.sect),
                selectinload(Character.artifact),
                selectinload(Character.ladder_record),
            )
        )
        character = await session.scalar(statement)
        if character is not None:
            self._ensure_character_compatibility(character)
        return character

    async def get_character_by_rank(self, session: AsyncSession, rank: int) -> Character | None:
        statement = (
            select(Character)
            .where(Character.current_ladder_rank == rank)
            .options(
                selectinload(Character.player),
                selectinload(Character.sect),
                selectinload(Character.artifact),
                selectinload(Character.ladder_record),
            )
        )
        character = await session.scalar(statement)
        if character is not None:
            self._ensure_character_compatibility(character)
        return character

    async def get_character_by_id(self, session: AsyncSession, character_id: int) -> Character | None:
        statement = (
            select(Character)
            .where(Character.id == character_id)
            .options(
                selectinload(Character.player),
                selectinload(Character.sect),
                selectinload(Character.artifact),
                selectinload(Character.ladder_record),
            )
        )
        character = await session.scalar(statement)
        if character is not None:
            self._ensure_character_compatibility(character)
        return character

    async def list_characters(self, session: AsyncSession) -> list[Character]:
        statement = select(Character).options(
            selectinload(Character.player),
            selectinload(Character.sect),
            selectinload(Character.artifact),
            selectinload(Character.ladder_record),
        )
        result = await session.scalars(statement)
        characters = list(result.all())
        for character in characters:
            self._ensure_character_compatibility(character)
        return characters

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
            retreat_mode="cultivation",
            is_traveling=False,
            last_idle_at=now,
            travel_started_at=now,
            travel_duration_minutes=0,
            travel_selected_duration_minutes=120,
            travel_atk_pct=0,
            travel_def_pct=0,
            travel_agi_pct=0,
            last_qi_recovered_at=now,
            sect_id=None,
            sect_joined_at=None,
            sect_last_left_at=None,
            sect_contribution_total=0,
            sect_contribution_weekly=0,
            sect_contribution_daily=0,
            sect_last_contribution_on=None,
            sect_last_settlement_on=None,
            sect_last_settlement_summary="",
            sect_task_refresh_on=None,
            sect_task_state_json="",
            lingshi=0,
            fate_key=fate.key,
            honor_tags_json="[]",
            faction="neutral",
            virtue=0,
            infamy=0,
            luck=self.fate_service.random_initial_luck(),
            bounty_soul=0,
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
            spirit_name=None,
            spirit_rename_used=False,
            spirit_json="",
            spirit_pending_json="",
            spirit_refining_until=None,
            spirit_refining_mode=None,
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
        if artifact is not None:
            artifact_atk_bonus, artifact_def_bonus, artifact_agi_bonus = self.spirit_service.effective_artifact_bonuses(artifact)
        else:
            artifact_atk_bonus, artifact_def_bonus, artifact_agi_bonus = (0, 0, 0)
        atk = stage.base_atk + artifact_atk_bonus
        defense = stage.base_def + artifact_def_bonus
        agility = stage.base_agi + artifact_agi_bonus
        atk = int(atk * (1 + (character.travel_atk_pct or 0) / 100))
        defense = int(defense * (1 + (character.travel_def_pct or 0) / 100))
        agility = int(agility * (1 + (character.travel_agi_pct or 0) / 100))
        atk = int(atk * self.fate_service.stat_multiplier(character.fate_key, "atk"))
        defense = int(defense * self.fate_service.stat_multiplier(character.fate_key, "def"))
        agility = int(agility * self.fate_service.stat_multiplier(character.fate_key, "agi"))
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
        faction_title: str = "",
        sect_name: str = "",
        sect_role: str = "",
        honor_tags: tuple[str, ...] = (),
        idle_minutes: int = 0,
        travel_minutes: int = 0,
    ) -> CharacterSnapshot:
        self._ensure_character_compatibility(character)
        stage = self.get_stage(character)
        artifact = character.artifact
        if artifact is not None:
            self.artifact_service.ensure_affix_slots(artifact)
            self.spirit_service.ensure_compatibility(artifact)
        stats = self.calculate_total_stats(character)
        fate = self.fate_service.get_fate(character.fate_key)
        spirit_name, spirit_tier_name, spirit_power_name = self.spirit_service.spirit_summary(artifact) if artifact is not None else ("未孕器灵", "", "")
        artifact_atk_bonus, artifact_def_bonus, artifact_agi_bonus = self.spirit_service.effective_artifact_bonuses(artifact) if artifact is not None else (0, 0, 0)
        return CharacterSnapshot(
            character_id=character.id,
            player_name=character.player.display_name,
            realm_index=stage.realm_index,
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
            artifact_power=self.spirit_service.artifact_power(artifact) if artifact else 0,
            artifact_atk_bonus=artifact_atk_bonus,
            artifact_def_bonus=artifact_def_bonus,
            artifact_agi_bonus=artifact_agi_bonus,
            spirit_name=spirit_name,
            spirit_tier_name=spirit_tier_name,
            spirit_power_name=spirit_power_name,
            sect_name=sect_name or (character.sect.name if character.sect is not None else ""),
            sect_role=sect_role,
            sect_contribution_daily=character.sect_contribution_daily or 0,
            sect_last_settlement_on=character.sect_last_settlement_on,
            sect_last_settlement_summary=character.sect_last_settlement_summary or "",
            lingshi=character.lingshi or 0,
            soul_shards=(artifact.soul_shards or 0) if artifact else 0,
            title=title or character.title,
            faction_key=character.faction,
            faction_name={"neutral": "中立", "righteous": "正道", "demonic": "魔道"}.get(character.faction, "中立"),
            faction_title=faction_title,
            virtue=character.virtue or 0,
            infamy=character.infamy or 0,
            luck=character.luck or 0,
            rewrite_chances=max(0, (character.luck or 0) // 100),
            bounty_soul=character.bounty_soul or 0,
            honor_tags=honor_tags,
            reincarnation_count=character.reincarnation_count,
            last_highlight_text=character.last_highlight_text,
            current_ladder_rank=character.current_ladder_rank,
            best_ladder_rank=character.best_ladder_rank,
            daily_pvp_attempts_left=max(0, 5 - character.daily_pvp_attempts_used),
            idle_minutes=idle_minutes,
            is_retreating=character.is_retreating,
            retreat_mode=character.retreat_mode,
            is_traveling=character.is_traveling,
            travel_minutes=travel_minutes,
            travel_duration_minutes=character.travel_duration_minutes,
            travel_selected_duration_minutes=character.travel_selected_duration_minutes,
            pg_best_score=character.pg_best_score or 0,
            pg_completions=character.pg_completions or 0,
            travel_atk_pct=character.travel_atk_pct,
            travel_def_pct=character.travel_def_pct,
            travel_agi_pct=character.travel_agi_pct,
        )

    def build_combatant(self, character: Character, *, title: str | None = None) -> CombatantSnapshot:
        snapshot = self.build_snapshot(character, title=title)
        fate = self.fate_service.get_fate(character.fate_key)
        return CombatantSnapshot(
            name=snapshot.player_name,
            atk=snapshot.total_atk,
            defense=snapshot.total_def,
            agility=snapshot.total_agi,
            max_hp=snapshot.total_def * 10,
            title=title or character.title,
            fate_name=snapshot.fate_name,
            affixes=tuple(self.artifact_service.get_active_affixes(character.artifact)) if character.artifact is not None else (),
            spirit_power=self.spirit_service.current_spirit_power(character.artifact) if character.artifact is not None else None,
            realm_index=snapshot.realm_index,
            damage_dealt_basis_points=fate.damage_dealt_basis_points,
            damage_taken_basis_points=fate.damage_taken_basis_points,
            damage_reduction_basis_points=fate.damage_reduction_basis_points,
            versus_higher_realm_damage_basis_points=fate.versus_higher_realm_damage_basis_points,
        )

    def can_reincarnate_today(self, character: Character) -> bool:
        return character.last_reincarnated_on != today_shanghai()

    def retreat_mode_name(self, retreat_mode: str) -> str:
        return self.RETREAT_MODE_NAMES.get(retreat_mode, "修炼")

    def start_retreat(self, character: Character, mode: str = "cultivation") -> RetreatActionResult:
        if character.is_retreating:
            return RetreatActionResult(False, "你已在洞府闭关中，无需再次入定。")
        if character.is_traveling:
            return RetreatActionResult(False, "你仍在外游历，需先归来结算，方可再入洞府。")
        if mode not in self.RETREAT_MODE_NAMES:
            return RetreatActionResult(False, "此等闭关法门尚未悟透。")
        now = now_shanghai()
        character.is_retreating = True
        character.retreat_mode = mode
        character.last_idle_at = now
        if mode == "soul":
            character.last_highlight_text = "方才封炉静坐，引本命灵韵淬炼器魂。"
            return RetreatActionResult(True, "你已合拢洞府石门，炉火微明，开始炼魂。")
        character.last_highlight_text = "方才入洞府闭关，静候灵气归体。"
        return RetreatActionResult(True, "你已封闭洞府，开始闭关修炼。")

    def stop_retreat(self, character: Character, settlement: IdleSettlement) -> RetreatActionResult:
        if not character.is_retreating:
            return RetreatActionResult(False, "你当前并未闭关，无需强行出关。", settlement)
        mode = character.retreat_mode
        character.is_retreating = False
        character.retreat_mode = "cultivation"
        if settlement.gained_cultivation > 0 or settlement.gained_soul > 0 or settlement.gained_luck > 0:
            pieces: list[str] = []
            if settlement.gained_cultivation > 0:
                pieces.append(f"修为增长 {settlement.gained_cultivation}")
            if settlement.gained_soul > 0:
                pieces.append(f"器魂凝成 {settlement.gained_soul}")
            if settlement.gained_luck > 0:
                pieces.append(f"气运增长 {settlement.gained_luck}")
            prefix = "方才开炉" if mode == "soul" else "方才出关"
            character.last_highlight_text = f"{prefix}，{'，'.join(pieces)}。"
        else:
            character.last_highlight_text = "方才出关，却觉灵气未满一周天。"
        if mode == "soul":
            return RetreatActionResult(True, "你已熄去炉火，此番炼魂所得已尽数归入本命。", settlement)
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
        character.retreat_mode = "cultivation"
        character.is_traveling = False
        character.last_idle_at = now
        character.travel_started_at = now
        character.travel_duration_minutes = 0
        character.travel_selected_duration_minutes = 120
        character.travel_atk_pct = 0
        character.travel_def_pct = 0
        character.travel_agi_pct = 0
        character.sect_id = None
        character.sect_joined_at = None
        character.sect_last_left_at = None
        character.sect_contribution_total = 0
        character.sect_contribution_weekly = 0
        character.sect_contribution_daily = 0
        character.sect_last_contribution_on = None
        character.sect_last_settlement_on = None
        character.sect_last_settlement_summary = ""
        character.sect_task_refresh_on = None
        character.sect_task_state_json = ""
        character.lingshi = 0
        character.last_qi_recovered_at = now
        character.fate_key = new_fate.key
        character.faction = "neutral"
        character.virtue = 0
        character.infamy = 0
        character.luck = self.fate_service.random_initial_luck()
        character.bounty_soul = 0
        character.last_bounty_growth_on = None
        character.last_robbery_at = None
        character.last_bounty_hunt_at = None
        character.last_bounty_defeated_on = None
        character.daily_pvp_attempts_used = 0
        character.last_pvp_reset_on = today
        character.reincarnation_count += 1
        character.last_reincarnated_on = today
        character.pg_invest_stat_level = 0
        character.pg_invest_affix_slots = 0
        character.pg_invest_spirit_unlocked = False
        character.last_highlight_text = f"轮回一转，再得命格「{new_fate.name}」。"
        if character.artifact is not None:
            character.artifact.reinforce_level = 0
            character.artifact.atk_bonus = 0
            character.artifact.def_bonus = 0
            character.artifact.agi_bonus = 0
            character.artifact.soul_shards = 0
            self.artifact_service.reset_affixes(character.artifact)
            character.artifact.spirit_name = None
            character.artifact.spirit_rename_used = False
            character.artifact.spirit_json = ""
            character.artifact.spirit_pending_json = ""
            character.artifact.spirit_refining_until = None
            character.artifact.spirit_refining_mode = None
        self.refresh_combat_power(character)

        broadcast_text = None
        if new_fate.broadcast_on_obtain:
            broadcast_text = f"【轮回惊世】{character.player.display_name} 轮回之后，再得传说命格「{new_fate.name}」。"
        return ReincarnationResult(True, "旧躯尽褪，命格已换。", character, new_fate.broadcast_on_obtain, broadcast_text)

    async def rewrite_fate(self, session: AsyncSession, character: Character) -> FateRewriteResult:
        self._ensure_character_compatibility(character)
        if character.luck < 100:
            return FateRewriteResult(False, "气运未满百数，还不足以逆转命盘。", character, False, None)

        new_fate = self.fate_service.roll_fate(exclude_key=character.fate_key)
        character.luck -= 100
        character.fate_key = new_fate.key
        character.last_highlight_text = f"方才逆天改命，命格重定为「{new_fate.name}」。"
        self.refresh_combat_power(character)

        broadcast_text = None
        if new_fate.broadcast_on_obtain:
            broadcast_text = f"【逆天改命】{character.player.display_name} 再得传说命格「{new_fate.name}」。"
        return FateRewriteResult(True, "你以百点气运强改命盘，旧命已去，新命已定。", character, new_fate.broadcast_on_obtain, broadcast_text)
