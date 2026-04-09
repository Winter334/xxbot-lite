from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.character import Character
from bot.services.character_service import CharacterService
from bot.services.combat_service import BattleResult, CombatService
from bot.utils.time_utils import today_shanghai


@dataclass(slots=True)
class ChallengeTarget:
    rank: int
    display_name: str
    realm_display: str
    combat_power: int


@dataclass(slots=True)
class LadderChallengeResult:
    success: bool
    message: str
    battle: BattleResult | None
    challenger_rank_before: int
    challenger_rank_after: int
    defender_rank_before: int | None
    defender_rank_after: int | None
    remaining_attempts: int
    reached_top_rank: bool = False


class LadderService:
    daily_attempt_limit = 5
    challenge_range = 5

    def __init__(self, character_service: CharacterService, combat_service: CombatService) -> None:
        self.character_service = character_service
        self.combat_service = combat_service

    def reset_daily_attempts_if_needed(self, character: Character) -> None:
        today = today_shanghai()
        if character.last_pvp_reset_on != today:
            character.daily_pvp_attempts_used = 0
            character.last_pvp_reset_on = today

    def remaining_attempts(self, character: Character) -> int:
        self.reset_daily_attempts_if_needed(character)
        return max(0, self.daily_attempt_limit - character.daily_pvp_attempts_used)

    async def get_challenge_targets(self, session: AsyncSession, character: Character) -> list[ChallengeTarget]:
        if character.current_ladder_rank <= 1:
            return []
        lower_bound = max(1, character.current_ladder_rank - self.challenge_range)
        statement = (
            select(Character.current_ladder_rank)
            .where(Character.current_ladder_rank >= lower_bound, Character.current_ladder_rank < character.current_ladder_rank)
            .order_by(Character.current_ladder_rank.asc())
        )
        ranks = list((await session.scalars(statement)).all())
        targets: list[ChallengeTarget] = []
        for rank in ranks:
            opponent = await self.character_service.get_character_by_rank(session, rank)
            if opponent is None:
                continue
            snapshot = self.character_service.build_snapshot(opponent)
            targets.append(ChallengeTarget(rank, snapshot.player_name, snapshot.realm_display, snapshot.combat_power))
        return targets

    async def challenge(self, session: AsyncSession, challenger: Character, target_rank: int) -> LadderChallengeResult:
        self.reset_daily_attempts_if_needed(challenger)
        rank_before = challenger.current_ladder_rank
        if self.remaining_attempts(challenger) <= 0:
            return LadderChallengeResult(False, "今日论道次数已尽。", None, rank_before, rank_before, None, None, 0)
        if target_rank >= rank_before or rank_before - target_rank > self.challenge_range:
            return LadderChallengeResult(False, "只能挑战自己前方五名之内的对手。", None, rank_before, rank_before, None, None, self.remaining_attempts(challenger))

        defender = await self.character_service.get_character_by_rank(session, target_rank)
        if defender is None:
            return LadderChallengeResult(False, "目标名次无人镇守，稍后再试。", None, rank_before, rank_before, None, None, self.remaining_attempts(challenger))

        challenger_snapshot = self.character_service.build_snapshot(challenger)
        defender_snapshot = self.character_service.build_snapshot(defender)
        challenger_fighter = self.character_service.build_combatant(challenger, title=challenger_snapshot.title)
        defender_fighter = self.character_service.build_combatant(defender, title=defender_snapshot.title)
        battle = self.combat_service.run_battle(challenger_fighter, defender_fighter, scene_tags=("scene_ladder",))

        challenger.daily_pvp_attempts_used += 1
        challenger.last_pvp_reset_on = today_shanghai()
        defender_rank_before = defender.current_ladder_rank
        reached_top_rank = False

        if battle.challenger_won:
            challenger.current_ladder_rank, defender.current_ladder_rank = defender.current_ladder_rank, challenger.current_ladder_rank
            challenger.best_ladder_rank = min(challenger.best_ladder_rank, challenger.current_ladder_rank)
            if challenger.ladder_record is not None:
                challenger.ladder_record.rank = challenger.current_ladder_rank
                challenger.ladder_record.wins += 1
                challenger.ladder_record.streak += 1
            if defender.ladder_record is not None:
                defender.ladder_record.rank = defender.current_ladder_rank
                defender.ladder_record.losses += 1
                defender.ladder_record.streak = 0
            challenger.last_highlight_text = f"方才斩落论道榜第 {defender_rank_before} 位。"
            defender.last_highlight_text = f"方才在论道中被 {challenger.player.display_name} 夺位。"
            reached_top_rank = challenger.current_ladder_rank == 1
            message = f"论道得胜，名次上升至第 {challenger.current_ladder_rank} 位。"
        else:
            if challenger.ladder_record is not None:
                challenger.ladder_record.losses += 1
                challenger.ladder_record.streak = 0
            if defender.ladder_record is not None:
                defender.ladder_record.wins += 1
                defender.ladder_record.streak += 1
            message = "鏖战十合未能夺位，此番论道判负。"

        return LadderChallengeResult(
            True,
            message,
            battle,
            rank_before,
            challenger.current_ladder_rank,
            defender_rank_before,
            defender.current_ladder_rank,
            self.remaining_attempts(challenger),
            reached_top_rank,
        )

    async def move_to_bottom(self, session: AsyncSession, character: Character) -> None:
        total = int((await session.scalar(select(func.count(Character.id)))) or 0)
        current_rank = character.current_ladder_rank
        if current_rank >= total:
            return
        statement = select(Character).where(Character.current_ladder_rank > current_rank).order_by(Character.current_ladder_rank.asc())
        trailing = list((await session.scalars(statement)).all())
        for entry in trailing:
            hydrated = await self.character_service.get_character_by_rank(session, entry.current_ladder_rank)
            if hydrated is None or hydrated.id == character.id:
                continue
            hydrated.current_ladder_rank -= 1
            if hydrated.ladder_record is not None:
                hydrated.ladder_record.rank = hydrated.current_ladder_rank
        character.current_ladder_rank = total
        if character.ladder_record is not None:
            character.ladder_record.rank = total
