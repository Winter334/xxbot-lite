from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.views.panel import (
    build_breakthrough_message,
    build_leaderboard_message,
    build_panel_message,
    build_reincarnation_message,
    run_private_tower_sequence,
)

if TYPE_CHECKING:
    from bot.main import XianBot


LEADERBOARD_CHOICES = [
    app_commands.Choice(name="论道榜", value="ladder"),
    app_commands.Choice(name="综合战力榜", value="power"),
    app_commands.Choice(name="同境战力榜", value="realm_power"),
    app_commands.Choice(name="通天塔榜", value="tower"),
    app_commands.Choice(name="本命法宝榜", value="artifact"),
    app_commands.Choice(name="境界榜", value="realm"),
]

PUBLIC_PANEL_DELETE_AFTER = 10 * 60


class XianCommands(commands.Cog):
    def __init__(self, bot: XianBot) -> None:
        self.bot = bot

    async def _send_with_broadcasts(
        self,
        interaction: discord.Interaction,
        payload,
        *,
        ephemeral: bool = False,
        delete_after: float | None = None,
    ) -> None:
        embed, view, broadcasts = payload
        await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral, delete_after=delete_after)
        for content in broadcasts:
            await self.bot.broadcast_service.broadcast(self.bot, content)

    @app_commands.command(name="修仙", description="打开你的修仙主面板。")
    async def panel(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_panel_message(
                self.bot,
                interaction.user.id,
                interaction.user.display_name,
                avatar_url=interaction.user.display_avatar.url,
            ),
            delete_after=PUBLIC_PANEL_DELETE_AFTER,
        )

    @app_commands.command(name="登塔", description="消耗 1 点气机，尝试冲击通天塔新高。")
    async def tower(self, interaction: discord.Interaction) -> None:
        await run_private_tower_sequence(self.bot, interaction, owner_user_id=interaction.user.id, display_name=interaction.user.display_name)

    @app_commands.command(name="突破", description="尝试突破当前境界。")
    async def breakthrough(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_breakthrough_message(self.bot, interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )

    @app_commands.command(name="榜单", description="查看一期榜单。")
    @app_commands.describe(category="要查看的榜单类型")
    @app_commands.choices(category=LEADERBOARD_CHOICES)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_leaderboard_message(
                self.bot,
                interaction.user.id,
                interaction.user.display_name,
                category=category.value if category else "ladder",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="面板", description="查看其他修士的公开面板。")
    @app_commands.describe(user="要查看的道友")
    async def inspect_panel(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_panel_message(
                self.bot,
                interaction.user.id,
                interaction.user.display_name,
                target_user_id=user.id,
                target_avatar_url=user.display_avatar.url,
            ),
            delete_after=PUBLIC_PANEL_DELETE_AFTER,
        )

    @app_commands.command(name="轮回", description="舍弃当前主线进度，轮回重修。")
    async def reincarnate(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_reincarnation_message(self.bot, interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )


async def setup(bot: XianBot) -> None:
    await bot.add_cog(XianCommands(bot))
