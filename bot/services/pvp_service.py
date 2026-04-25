from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.arena_state import ArenaState
from bot.models.character import Character
from bot.services.character_service import CharacterService
from bot.services.combat_service import BattleResult, CombatService


@dataclass(slots=True)
class SparChallengeResult:
    success: bool
    message: str
    battle: BattleResult | None


@dataclass(slots=True)
class ArenaStatus:
    champion_character_id: int | None
    stake_soul: int
    pot_soul: int
    win_streak: int

    @property
    def has_champion(self) -> bool:
        return self.champion_character_id is not None and self.stake_soul > 0 and self.pot_soul > 0


@dataclass(slots=True)
class ArenaOpenResult:
    success: bool
    message: str
    stake_soul: int
    pot_soul: int


@dataclass(slots=True)
class ArenaChallengeResult:
    success: bool
    message: str
    battle: BattleResult | None
    current_champion_name: str | None
    stake_soul: int
    pot_soul: int
    win_streak: int
    champion_changed: bool


@dataclass(slots=True)
class ArenaClaimResult:
    success: bool
    message: str
    claimed_soul: int
    win_streak: int


class PvpService:
    public_arena_key = "public"

    def __init__(self, character_service: CharacterService, combat_service: CombatService) -> None:
        self.character_service = character_service
        self.combat_service = combat_service
        self._pending_spar_users: set[int] = set()
        self.arena_lock = asyncio.Lock()

    def can_pvp(self, character: Character, *, actor_label: str, action_name: str) -> tuple[bool, str | None]:
        if character.is_retreating:
            return False, f"{actor_label}仍在闭关，暂不可{action_name}。"
        if character.is_traveling:
            return False, f"{actor_label}仍在游历，暂不可{action_name}。"
        return True, None

    def can_spar(self, character: Character, *, actor_label: str) -> tuple[bool, str | None]:
        return self.can_pvp(character, actor_label=actor_label, action_name="切磋")

    def reserve_spar_request(self, challenger_user_id: int, defender_user_id: int) -> bool:
        if challenger_user_id in self._pending_spar_users or defender_user_id in self._pending_spar_users:
            return False
        self._pending_spar_users.add(challenger_user_id)
        self._pending_spar_users.add(defender_user_id)
        return True

    def release_spar_request(self, challenger_user_id: int, defender_user_id: int) -> None:
        self._pending_spar_users.discard(challenger_user_id)
        self._pending_spar_users.discard(defender_user_id)

    def spar(self, challenger: Character, defender: Character) -> SparChallengeResult:
        if challenger.id == defender.id:
            return SparChallengeResult(False, "你还不至于自己和自己切磋。", None)

        allowed, reason = self.can_spar(challenger, actor_label="你")
        if not allowed:
            return SparChallengeResult(False, reason or "当前不可切磋。", None)
        allowed, reason = self.can_spar(defender, actor_label="对方")
        if not allowed:
            return SparChallengeResult(False, reason or "对方当前不可切磋。", None)

        challenger_snapshot = self.character_service.build_snapshot(challenger)
        defender_snapshot = self.character_service.build_snapshot(defender)
        challenger_fighter = self.character_service.build_combatant(challenger, title=challenger_snapshot.title)
        defender_fighter = self.character_service.build_combatant(defender, title=defender_snapshot.title)
        battle = self.combat_service.run_battle(
            challenger_fighter,
            defender_fighter,
            scene_tags=("scene_spar", "scene_pvp"),
        )

        if battle.challenger_won:
            challenger.last_highlight_text = f"方才与 {defender.player.display_name} 切磋得胜。"
            defender.last_highlight_text = f"方才与 {challenger.player.display_name} 切磋落败。"
            message = f"切磋已毕，{challenger.player.display_name} 更胜一筹。"
        else:
            challenger.last_highlight_text = f"方才与 {defender.player.display_name} 切磋落败。"
            defender.last_highlight_text = f"方才与 {challenger.player.display_name} 切磋得胜。"
            message = f"切磋已毕，{defender.player.display_name} 更胜一筹。"
        return SparChallengeResult(True, message, battle)

    async def get_arena_status(self, session: AsyncSession) -> tuple[ArenaStatus, Character | None]:
        arena, champion = await self._resolve_arena(session)
        return self._build_arena_status(arena), champion

    async def open_arena(self, session: AsyncSession, challenger: Character, stake_soul: int) -> ArenaOpenResult:
        if stake_soul <= 0:
            return ArenaOpenResult(False, "开擂至少要押上 1 点器魂。", 0, 0)

        allowed, reason = self.can_pvp(challenger, actor_label="你", action_name="开擂")
        if not allowed:
            return ArenaOpenResult(False, reason or "你当前不可开擂。", 0, 0)

        arena, champion = await self._resolve_arena(session)
        if champion is not None:
            if champion.id == challenger.id:
                return ArenaOpenResult(False, "你已经在擂台上了。", arena.stake_soul, arena.pot_soul)
            return ArenaOpenResult(False, "当前已有擂主镇守，请先攻擂。", arena.stake_soul, arena.pot_soul)

        if challenger.artifact.soul_shards < stake_soul:
            return ArenaOpenResult(False, f"器魂不足，当前仅有 {challenger.artifact.soul_shards}。", 0, 0)

        challenger.artifact.soul_shards -= stake_soul
        arena.champion_character_id = challenger.id
        arena.stake_soul = stake_soul
        arena.pot_soul = stake_soul
        arena.win_streak = 0
        challenger.last_highlight_text = f"方才押上 {stake_soul} 器魂，登上了单擂台。"
        return ArenaOpenResult(True, f"{challenger.player.display_name} 押上 {stake_soul} 器魂，登上了单擂台。", arena.stake_soul, arena.pot_soul)

    async def challenge_arena(self, session: AsyncSession, challenger: Character) -> ArenaChallengeResult:
        allowed, reason = self.can_pvp(challenger, actor_label="你", action_name="攻擂")
        if not allowed:
            return ArenaChallengeResult(False, reason or "你当前不可攻擂。", None, None, 0, 0, 0, False)

        arena, defender = await self._resolve_arena(session)
        status = self._build_arena_status(arena)
        if defender is None or not status.has_champion:
            return ArenaChallengeResult(False, "当前擂台无人镇守。", None, None, 0, 0, 0, False)
        if defender.id == challenger.id:
            return ArenaChallengeResult(False, "你当前就是擂主，无需再攻擂。", None, defender.player.display_name, status.stake_soul, status.pot_soul, status.win_streak, False)
        if challenger.artifact.soul_shards < status.stake_soul:
            return ArenaChallengeResult(
                False,
                f"攻擂需要等额押上 {status.stake_soul} 器魂，你当前仅有 {challenger.artifact.soul_shards}。",
                None,
                defender.player.display_name,
                status.stake_soul,
                status.pot_soul,
                status.win_streak,
                False,
            )

        challenger.artifact.soul_shards -= status.stake_soul
        arena.pot_soul += status.stake_soul

        defender_allowed, defender_reason = self.can_pvp(defender, actor_label="擂主", action_name="守擂")
        if not defender_allowed:
            arena.champion_character_id = challenger.id
            arena.win_streak = 1
            challenger.last_highlight_text = f"方才接手单擂台，当前擂池为 {arena.pot_soul} 器魂。"
            defender.last_highlight_text = f"方才因无法应战而失了擂台，被 {challenger.player.display_name} 接手。"
            return ArenaChallengeResult(
                True,
                f"擂主当前无法应战，判定弃擂。{challenger.player.display_name} 直接接过擂台，当前擂池为 {arena.pot_soul} 器魂。",
                None,
                challenger.player.display_name,
                arena.stake_soul,
                arena.pot_soul,
                arena.win_streak,
                True,
            )

        challenger_snapshot = self.character_service.build_snapshot(challenger)
        defender_snapshot = self.character_service.build_snapshot(defender)
        challenger_fighter = self.character_service.build_combatant(challenger, title=challenger_snapshot.title)
        defender_fighter = self.character_service.build_combatant(defender, title=defender_snapshot.title)
        battle = self.combat_service.run_battle(
            challenger_fighter,
            defender_fighter,
            scene_tags=("scene_arena", "scene_pvp"),
        )

        if battle.challenger_won:
            arena.champion_character_id = challenger.id
            arena.win_streak = 1
            challenger.last_highlight_text = f"方才夺下单擂台，当前擂池为 {arena.pot_soul} 器魂。"
            defender.last_highlight_text = f"方才在擂台战中失擂，被 {challenger.player.display_name} 夺位。"
            return ArenaChallengeResult(
                True,
                f"{challenger.player.display_name} 攻擂得手，夺下擂台。当前可收擂离场，也可继续接受挑战。",
                battle,
                challenger.player.display_name,
                arena.stake_soul,
                arena.pot_soul,
                arena.win_streak,
                True,
            )

        arena.win_streak += 1
        defender.last_highlight_text = f"方才守住擂台，当前擂池为 {arena.pot_soul} 器魂。"
        challenger.last_highlight_text = f"方才攻擂失手，败给了 {defender.player.display_name}。"
        return ArenaChallengeResult(
            True,
            f"{defender.player.display_name} 守擂成功，当前可收擂离场，也可继续接受挑战。",
            battle,
            defender.player.display_name,
            arena.stake_soul,
            arena.pot_soul,
            arena.win_streak,
            False,
        )

    async def claim_arena(self, session: AsyncSession, challenger: Character) -> ArenaClaimResult:
        arena, champion = await self._resolve_arena(session)
        status = self._build_arena_status(arena)
        if champion is None or not status.has_champion:
            return ArenaClaimResult(False, "当前擂台无人镇守，无需收擂。", 0, 0)
        if champion.id != challenger.id:
            return ArenaClaimResult(False, "只有当前擂主才能收擂离场。", 0, status.win_streak)

        claimed_soul = arena.pot_soul
        win_streak = arena.win_streak
        challenger.artifact.soul_shards += claimed_soul
        self._clear_arena(arena)
        challenger.last_highlight_text = f"方才收擂离场，带走了 {claimed_soul} 器魂。"
        return ArenaClaimResult(True, f"{challenger.player.display_name} 收擂离场，带走了 {claimed_soul} 器魂。", claimed_soul, win_streak)

    async def _resolve_arena(self, session: AsyncSession) -> tuple[ArenaState, Character | None]:
        arena = await self._get_or_create_arena(session)
        champion: Character | None = None
        if arena.champion_character_id is not None:
            champion = await self.character_service.get_character_by_id(session, arena.champion_character_id)
        if champion is None and arena.champion_character_id is not None:
            self._clear_arena(arena)
        elif champion is None and (arena.stake_soul or arena.pot_soul or arena.win_streak):
            self._clear_arena(arena)
        elif champion is not None and (arena.stake_soul <= 0 or arena.pot_soul <= 0):
            self._clear_arena(arena)
            champion = None
        return arena, champion

    async def _get_or_create_arena(self, session: AsyncSession) -> ArenaState:
        statement = select(ArenaState).where(ArenaState.arena_key == self.public_arena_key)
        arena = await session.scalar(statement)
        if arena is None:
            arena = ArenaState(arena_key=self.public_arena_key)
            session.add(arena)
            await session.flush()
        return arena

    def _build_arena_status(self, arena: ArenaState) -> ArenaStatus:
        return ArenaStatus(
            champion_character_id=arena.champion_character_id,
            stake_soul=arena.stake_soul,
            pot_soul=arena.pot_soul,
            win_streak=arena.win_streak,
        )

    def _clear_arena(self, arena: ArenaState) -> None:
        arena.champion_character_id = None
        arena.stake_soul = 0
        arena.pot_soul = 0
        arena.win_streak = 0
