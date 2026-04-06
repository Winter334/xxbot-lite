from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.views.panel import (
    ArtifactOverviewView,
    ArtifactRefineView,
    ArtifactReinforceView,
    LeaderboardView,
    PanelView,
    ReincarnationConfirmView,
    RetreatView,
    TowerRunView,
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
        pending_slots=[SimpleNamespace(slot=i, affix_id=("x" if i in (2, 4) else None)) for i in range(1, 6)],
        current_slots=[SimpleNamespace(slot=i, unlock_level=i * 10, unlocked=True) for i in range(1, 6)],
    )
    challenge_targets = [SimpleNamespace(rank=i, display_name=f"修士{i}") for i in range(1, 6)]

    for view in (
        PanelView(1),
        ReincarnationConfirmView(1),
        TowerRunView(1, can_retry=True),
        ArtifactOverviewView(1),
        ArtifactReinforceView(1),
        ArtifactRefineView(1, panel_state),
        RetreatView(1, is_retreating=False),
        LeaderboardView(1, "power", []),
        LeaderboardView(1, "ladder", challenge_targets),
    ):
        _assert_component_limits(view)
