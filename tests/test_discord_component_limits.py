from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.views.panel import (
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


@pytest.mark.asyncio
async def test_views_respect_discord_component_limits() -> None:
    panel_state = SimpleNamespace(
        has_pending=True,
        pending_slots=[SimpleNamespace(slot=i, affix_id=("x" if i in (2, 4) else None), unlocked=True) for i in range(1, 6)],
        current_slots=[SimpleNamespace(slot=i, unlock_level=i * 10, unlocked=True) for i in range(1, 6)],
    )
    challenge_targets = [SimpleNamespace(rank=i, display_name=f"修士{i}") for i in range(1, 6)]

    for view in (
        PanelView(1),
        ReincarnationConfirmView(1),
        FateRewriteConfirmView(1, can_confirm=True),
        TowerRunView(1, can_retry=True),
        ArtifactOverviewView(1),
        ArtifactReinforceView(1),
        ArtifactRefineView(1, panel_state),
        SectOverviewView(1, has_sect=False, can_create=True, has_joinable=True),
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
        RetreatView(1, is_retreating=False),
        TravelView(
            1,
            snapshot=SimpleNamespace(
                is_traveling=False,
                travel_selected_duration_minutes=120,
            ),
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
        LeaderboardView(1, "power", []),
        LeaderboardView(1, "ladder", challenge_targets),
    ):
        _assert_component_limits(view)
