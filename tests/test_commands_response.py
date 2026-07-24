from __future__ import annotations

import asyncio

import pytest

from bot.commands.xian import XianCommands


class _DoneResponse:
    def is_done(self) -> bool:
        return True


class _Followup:
    def __init__(self) -> None:
        self.kwargs = None

    async def send(self, **kwargs):
        self.kwargs = kwargs
        return object()


class _Interaction:
    def __init__(self) -> None:
        self.response = _DoneResponse()
        self.followup = _Followup()


@pytest.mark.asyncio
async def test_deferred_response_uses_followup_without_delete_after_kwarg() -> None:
    command = XianCommands(object())
    interaction = _Interaction()
    deleted = []

    async def fake_delete_later(message, delay: float) -> None:
        deleted.append((message, delay))

    command._delete_followup_later = fake_delete_later

    await command._send_response(interaction, embed=object(), delete_after=123)
    await asyncio.sleep(0)

    assert "delete_after" not in interaction.followup.kwargs
    assert interaction.followup.kwargs["wait"] is True
    assert deleted and deleted[0][1] == 123
