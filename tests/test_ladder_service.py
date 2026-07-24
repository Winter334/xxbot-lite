from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.services.combat_service import ActionLog
from bot.services.ladder_service import LadderService
from bot.ui.panel import build_battle_report_pages


@pytest.mark.asyncio
async def test_new_player_ladder_rank_ignores_npc_placeholder(session_factory, services) -> None:
    async with session_factory() as session:
        first = await services.character.get_or_create_character(session, 7001, "真人一")
        npc = await services.character.get_or_create_character(session, 97001, "游荡散修")
        npc.character.is_npc = True
        npc.character.current_ladder_rank = 9000
        npc.character.best_ladder_rank = 9000
        if npc.character.ladder_record is not None:
            npc.character.ladder_record.rank = 9000
        await session.flush()

        newcomer = await services.character.get_or_create_character(session, 7002, "真人二")

        assert first.character.current_ladder_rank == 1
        assert newcomer.character.current_ladder_rank == 2


@pytest.mark.asyncio
async def test_challenge_targets_deduplicate_dirty_duplicate_ranks(session_factory, services) -> None:
    async with session_factory() as session:
        first = await services.character.get_or_create_character(session, 7101, "前席甲")
        duplicate = await services.character.get_or_create_character(session, 7102, "前席乙")
        challenger = await services.character.get_or_create_character(session, 7103, "挑战者")
        duplicate.character.current_ladder_rank = first.character.current_ladder_rank
        if duplicate.character.ladder_record is not None:
            duplicate.character.ladder_record.rank = duplicate.character.current_ladder_rank
        await session.flush()

        ladder = LadderService(services.character, services.combat)
        targets = await ladder.get_challenge_targets(session, challenger.character)

        assert [target.rank for target in targets] == [1]


def test_ladder_battle_report_pages_keep_every_round() -> None:
    battle = SimpleNamespace(
        rounds=40,
        reached_round_limit=False,
        logs=[
            ActionLog(
                round_no=round_no,
                actor_name="甲",
                target_name="乙",
                dodged=False,
                critical=False,
                damage=1,
                target_hp_after=1000 - round_no,
                text=f"第 {round_no} 回合完整复盘：" + "灵机交错" * 24,
            )
            for round_no in range(1, 41)
        ],
    )

    pages = build_battle_report_pages(battle)
    rendered = "\n".join(block for page in pages for block in page)

    assert len(pages) > 1
    for round_no in range(1, 41):
        assert f"**第 {round_no} 回合**" in rendered
