from __future__ import annotations

import pytest

from bot.models.character import Character
from bot.services.combat_service import BattleResult
from bot.services.pvp_service import PvpService, apply_arena_streak_honor, arena_streak_honor


def test_arena_streak_honor_thresholds() -> None:
    assert arena_streak_honor(9) is None
    assert arena_streak_honor(10) == "十连胜"
    assert arena_streak_honor(19) == "十连胜"
    assert arena_streak_honor(20) == "二十连胜"
    assert arena_streak_honor(99) == "九十连胜"
    assert arena_streak_honor(100) == "霸主"
    assert arena_streak_honor(110) == "霸主"


def test_apply_keeps_highest_streak_and_other_honors() -> None:
    character = Character()
    character.honor_tags_json = "[]"
    character.add_honor_tag("见过天碑")

    assert apply_arena_streak_honor(character, 9) is None
    assert character.stored_honor_tags() == ("见过天碑",)

    assert apply_arena_streak_honor(character, 10) == "十连胜"
    assert character.stored_honor_tags() == ("见过天碑", "十连胜")
    assert apply_arena_streak_honor(character, 10) is None

    assert apply_arena_streak_honor(character, 20) == "二十连胜"
    assert character.stored_honor_tags() == ("见过天碑", "二十连胜")
    assert apply_arena_streak_honor(character, 12) is None
    assert character.stored_honor_tags() == ("见过天碑", "二十连胜")

    assert apply_arena_streak_honor(character, 100) == "霸主"
    assert character.stored_honor_tags() == ("见过天碑", "霸主")
    assert apply_arena_streak_honor(character, 110) is None
    assert character.stored_honor_tags() == ("见过天碑", "霸主")


class _StubCombat:
    def __init__(self, *, challenger_won: bool) -> None:
        self.challenger_won = challenger_won

    def run_battle(self, *args, **kwargs) -> BattleResult:
        winner, loser = ("甲", "乙") if self.challenger_won else ("乙", "甲")
        return BattleResult(
            challenger_won=self.challenger_won,
            winner_name=winner,
            loser_name=loser,
            rounds=1,
            reached_round_limit=False,
            logs=[],
            challenger_max_hp=100,
            defender_max_hp=100,
            challenger_hp_after=100 if self.challenger_won else 0,
            defender_hp_after=0 if self.challenger_won else 100,
        )


@pytest.mark.asyncio
async def test_ten_defenses_grant_ten_win_honor(session_factory, services) -> None:
    pvp = PvpService(services.character, _StubCombat(challenger_won=False))
    async with session_factory() as session:
        champion = await services.character.get_or_create_character(session, 8001, "擂主")
        challenger = await services.character.get_or_create_character(session, 8002, "攻擂者")
        champion.character.artifact.soul_shards = 100
        challenger.character.artifact.soul_shards = 100
        opened = await pvp.open_arena(session, champion.character, 1)
        assert opened.success

        for _ in range(9):
            result = await pvp.challenge_arena(session, challenger.character)
            assert result.success
            assert "十连胜" not in champion.character.stored_honor_tags()
            assert "得荣誉" not in result.message

        result = await pvp.challenge_arena(session, challenger.character)
        assert result.success
        assert result.win_streak == 10
        assert champion.character.stored_honor_tags() == ("十连胜",)
        assert "得荣誉「十连胜」" in result.message
