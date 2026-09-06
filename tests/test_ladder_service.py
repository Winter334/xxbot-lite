from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.services.character_service import CharacterSnapshot
from bot.services.combat_service import ActionLog
from bot.services.ladder_service import LadderService
from bot.services.faction_service import FactionActionResult
from bot.ui.panel import build_battle_report_pages, build_faction_battle_embed, build_public_battle_report_embed
from bot.views.panel import PublicBattleReportView


def _snapshot(name: str, character_id: int) -> CharacterSnapshot:
    return CharacterSnapshot(
        character_id=character_id,
        player_name=name,
        realm_index=1,
        realm_display="炼气·前期",
        cultivation=0,
        cultivation_max=100,
        highest_floor=0,
        historical_highest_floor=0,
        qi_current=5,
        qi_max=5,
        total_atk=10,
        total_def=10,
        total_agi=10,
        combat_power=40,
        fate_name="凡骨",
        fate_rarity="common",
        fate_summary="无",
        artifact_name="木剑",
        artifact_level=0,
        artifact_power=0,
        artifact_atk_bonus=0,
        artifact_def_bonus=0,
        artifact_agi_bonus=0,
        spirit_name="未孕器灵",
        spirit_tier_name="",
        spirit_power_name="",
        sect_name="",
        sect_role="",
        sect_contribution_daily=0,
        sect_last_settlement_on=None,
        sect_last_settlement_summary="",
        lingshi=0,
        soul_shards=0,
        title="未立尊号",
        faction_key="neutral",
        faction_name="中立",
        faction_title="",
        virtue=0,
        infamy=0,
        luck=0,
        rewrite_chances=0,
        bounty_soul=0,
        honor_tags=(),
        reincarnation_count=0,
        last_highlight_text="",
        current_ladder_rank=character_id,
        best_ladder_rank=character_id,
        daily_pvp_attempts_left=5,
        idle_minutes=0,
        is_retreating=False,
        retreat_mode="cultivation",
        is_traveling=False,
        travel_minutes=0,
        travel_duration_minutes=0,
        travel_selected_duration_minutes=120,
        travel_atk_pct=0,
        travel_def_pct=0,
        travel_agi_pct=0,
        pg_total_score=0,
        pg_best_score=0,
        pg_completions=0,
    )


def _long_battle(rounds: int = 40):
    return SimpleNamespace(
        challenger_won=True,
        winner_name="甲",
        loser_name="乙",
        rounds=rounds,
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
            for round_no in range(1, rounds + 1)
        ],
        challenger_max_hp=1000,
        defender_max_hp=1000,
        challenger_hp_after=900,
        defender_hp_after=0,
    )


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
    battle = _long_battle()

    pages = build_battle_report_pages(battle)
    rendered = "\n".join(block for page in pages for block in page)

    assert len(pages) > 1
    for round_no in range(1, 41):
        assert f"**第 {round_no} 回合**" in rendered


def test_public_battle_report_embed_shows_page_index() -> None:
    battle = _long_battle()

    embed = build_public_battle_report_embed(
        _snapshot("甲", 1),
        _snapshot("乙", 2),
        battle,
        mode="ladder",
        summary_lines=["名次：`#2 -> #1`"],
        report_page=1,
    )

    overview = next(field for field in embed.fields if field.name == "战斗总览")
    assert "战报页：`2/" in overview.value
    assert embed.footer.text == "仅对战双方可翻页；所有人均可查看当前页。"


def test_public_faction_battle_report_embed_uses_full_paged_log() -> None:
    battle = _long_battle()

    for mode, title in (("bounty", "悬赏 · 完整战报"), ("robbery", "劫掠 · 完整战报")):
        embed = build_public_battle_report_embed(
            _snapshot("甲", 1),
            _snapshot("乙", 2),
            battle,
            mode=mode,
            summary_lines=["目标：**乙**"],
            report_page=0,
        )
        assert embed.title == title
        assert any(field.name.startswith("完整战报") for field in embed.fields)
        assert "**第 1 回合**" in "\n".join(field.value for field in embed.fields if field.name.startswith("完整战报"))


def test_private_faction_battle_embed_keeps_full_report() -> None:
    battle = _long_battle(2)
    result = FactionActionResult(True, "劫掠得手，所夺资源已尽归己身。", battle, soul_delta=12, target_name="乙")

    embed = build_faction_battle_embed(
        _snapshot("甲", 1),
        _snapshot("乙", 2),
        result,
        title="劫掠",
        summary_lines=["目标：**乙**", "器魂：`+12`"],
    )

    assert embed.title == "甲 · 劫掠"
    assert embed.description == "劫掠得手，所夺资源已尽归己身。"
    assert any(field.name.startswith("完整战报") for field in embed.fields)
    overview = next(field for field in embed.fields if field.name == "战斗总览")
    assert "目标：**乙**" in overview.value
    assert "器魂：`+12`" in overview.value
    assert not any(field.name == "战报截取" for field in embed.fields)


@pytest.mark.asyncio
async def test_public_battle_report_view_allows_only_participants() -> None:
    battle = _long_battle(1)
    view = PublicBattleReportView(
        _snapshot("甲", 1),
        _snapshot("乙", 2),
        battle,
        mode="spar",
        summary_lines=[],
        allowed_user_ids=(1001, 1002),
    )

    class Response:
        def __init__(self) -> None:
            self.message = None

        async def send_message(self, message, *, ephemeral=False):
            self.message = (message, ephemeral)

    class Interaction:
        def __init__(self, user_id: int) -> None:
            self.user = SimpleNamespace(id=user_id)
            self.response = Response()

    participant = Interaction(1001)
    outsider = Interaction(9999)

    assert await view.interaction_check(participant) is True
    assert await view.interaction_check(outsider) is False
    assert outsider.response.message == ("只有对战双方可翻页。", True)
