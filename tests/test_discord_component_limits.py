from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from bot.views.panel import (
    ArenaBoardView,
    ArtifactOverviewView,
    ArtifactRefineView,
    ArtifactReinforceView,
    FactionView,
    FateRewriteConfirmView,
    LeaderboardView,
    PanelView,
    ReincarnationConfirmView,
    RetreatView,
    SectDirectoryView,
    SectOverviewView,
    SectSiteActionView,
    SparInviteView,
    SpiritOverviewView,
    TowerRunView,
    TravelView,
)


def _assert_component_limits(view) -> None:
    rows = view.to_components()
    counts = [len(row["components"]) for row in rows]
    assert len(rows) <= 5
    assert sum(counts) <= 25
    assert all(count <= 5 for count in counts)


def _select_children(view) -> list[discord.ui.Select]:
    return [child for child in view.children if isinstance(child, discord.ui.Select)]


@pytest.mark.asyncio
async def test_views_respect_discord_component_limits() -> None:
    panel_state = SimpleNamespace(
        has_pending=True,
        pending_slots=[SimpleNamespace(slot=i, affix_id=("x" if i in (2, 4) else None), unlocked=True) for i in range(1, 6)],
        current_slots=[SimpleNamespace(slot=i, unlock_level=i * 10, unlocked=True) for i in range(1, 6)],
    )
    challenge_targets = [
        SimpleNamespace(rank=i, display_name=f"修士{i}", realm_display="筑基前期", combat_power=1000 + i)
        for i in range(1, 6)
    ]

    for view in (
        PanelView(1),
        ReincarnationConfirmView(1),
        FateRewriteConfirmView(1, can_confirm=True),
        TowerRunView(1, can_retry=True),
        ArtifactOverviewView(1),
        ArtifactReinforceView(1),
        ArtifactRefineView(1, panel_state),
        SectOverviewView(1, has_sect=False, can_create=True, has_joinable=True, overview=None),
        SectOverviewView(
            1,
            has_sect=True,
            can_create=False,
            has_joinable=False,
            overview=SimpleNamespace(
                members=[SimpleNamespace(character_id=1, display_name=f"门人{i}", role_name="弟子", realm_display="筑基前期", contribution_weekly=10) for i in range(1, 6)]
            ),
        ),
        SectDirectoryView(1, [SimpleNamespace(sect_id=1, name="青岚宗", faction_name="中立", member_count=3, owner_site_count=1)]),
        SectSiteActionView(1, sites=[SimpleNamespace(site_id=1, site_name="青云灵矿", site_type_name="灵矿", owner_name="青岚宗")], selected_site_id=1),
        SpiritOverviewView(
            1,
            SimpleNamespace(
                can_start_nurture=False,
                can_start_reforge=True,
                can_collect=False,
                can_accept_pending=False,
                can_discard_pending=False,
                can_rename=True,
            ),
        ),
        RetreatView(1, snapshot=SimpleNamespace(is_retreating=False)),
        TravelView(
            1,
            snapshot=SimpleNamespace(
                is_traveling=False,
                travel_selected_duration_minutes=120,
            ),
        ),
        ArenaBoardView(SimpleNamespace(), has_champion=True),
        SparInviteView(
            SimpleNamespace(pvp_service=SimpleNamespace(release_spar_request=lambda *_: None)),
            challenger_user_id=1,
            challenger_display_name="甲",
            defender_user_id=2,
            defender_display_name="乙",
        ),
        FactionView(
            1,
            snapshot=SimpleNamespace(
                faction_key="neutral",
                faction_name="中立",
                faction_title="",
            ),
            targets=[],
        ),
        FactionView(
            1,
            snapshot=SimpleNamespace(
                faction_key="demonic",
                faction_name="魔道",
                faction_title="",
            ),
            targets=[
                SimpleNamespace(
                    character_id=i,
                    display_name=f"魔修{i}",
                    faction_name="魔道",
                    realm_display="筑基前期",
                    luck=i,
                    soul=i * 2,
                    bounty_soul=0,
                )
                for i in range(1, 31)
            ],
            robbery_page=1,
        ),
        LeaderboardView(1, "power", []),
        LeaderboardView(1, "ladder", challenge_targets),
    ):
        _assert_component_limits(view)


@pytest.mark.asyncio
async def test_ladder_view_uses_previous_rank_button_and_select() -> None:
    challenge_targets = [
        SimpleNamespace(rank=i, display_name=f"修士{i}", realm_display="筑基前期", combat_power=1000 + i)
        for i in range(6, 11)
    ]
    view = LeaderboardView(1, "ladder", challenge_targets)

    button_labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
    assert "挑战前一位 #10 修士10" in button_labels
    selects = _select_children(view)
    assert len(selects) == 1
    assert [option.value for option in selects[0].options] == [str(i) for i in range(6, 11)]


@pytest.mark.asyncio
async def test_demonic_faction_view_paginates_robbery_targets() -> None:
    targets = [
        SimpleNamespace(
            character_id=i,
            display_name=f"魔修{i}",
            faction_name="魔道",
            realm_display="筑基前期",
            luck=i,
            soul=i * 2,
            bounty_soul=0,
        )
        for i in range(1, 31)
    ]
    view = FactionView(
        1,
        snapshot=SimpleNamespace(
            faction_key="demonic",
            faction_name="魔道",
            faction_title="",
        ),
        targets=targets,
        robbery_page=1,
    )

    selects = _select_children(view)
    assert len(selects) == 1
    assert [option.value for option in selects[0].options] == [str(i) for i in range(26, 31)]
    buttons = {child.label: child for child in view.children if isinstance(child, discord.ui.Button)}
    assert buttons["上一页"].disabled is False
    assert buttons["下一页"].disabled is True
    assert "2/2" in buttons
