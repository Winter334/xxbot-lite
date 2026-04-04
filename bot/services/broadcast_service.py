from __future__ import annotations

import discord

from bot.config import Settings


class BroadcastService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def broadcast(self, client: discord.Client, content: str | None) -> None:
        if not content or not self.settings.broadcast_enabled:
            return
        channel = client.get_channel(self.settings.broadcast_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(content)
