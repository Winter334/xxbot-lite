from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.views.panel import (
    build_arena_challenge_message,
    build_arena_claim_message,
    build_arena_message,
    build_open_arena_message,
    build_breakthrough_message,
    build_leaderboard_message,
    build_panel_message,
    build_reincarnation_confirm_message,
    build_spar_request_message,
    build_travel_message,
    run_private_tower_sequence,
    send_public_battle_animation,
)
from bot.views.proving_ground import build_pg_entry_message

if TYPE_CHECKING:
    from bot.main import XianBot


LEADERBOARD_CHOICES = [
    app_commands.Choice(name="论道榜", value="ladder"),
    app_commands.Choice(name="综合战力榜", value="power"),
    app_commands.Choice(name="同境战力榜", value="realm_power"),
    app_commands.Choice(name="通天塔榜", value="tower"),
    app_commands.Choice(name="本命法宝榜", value="artifact"),
    app_commands.Choice(name="境界榜", value="realm"),
    app_commands.Choice(name="正道榜", value="righteous"),
    app_commands.Choice(name="魔道榜", value="demonic"),
    app_commands.Choice(name="悬赏榜", value="bounty"),
    app_commands.Choice(name="证道积分榜", value="proving_ground"),
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

    async def _send_action_with_broadcasts(self, interaction: discord.Interaction, payload) -> None:
        embed, view, broadcasts, success = payload
        await interaction.response.send_message(embed=embed, view=view, ephemeral=not success)
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

    @app_commands.command(name="证道", description="踏入证道战场，以裸身面板闯关构筑（渡劫圆满可用）。")
    async def proving_ground(self, interaction: discord.Interaction) -> None:
        embed, view = await build_pg_entry_message(self.bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="突破", description="尝试突破当前境界。")
    async def breakthrough(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_breakthrough_message(self.bot, interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )

    @app_commands.command(name="\u6e38\u5386", description="\u6253\u5f00\u6e38\u5386\u9762\u677f\uff0c\u76f4\u63a5\u5916\u51fa\u649e\u673a\u7f18\u3002")
    async def travel(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_travel_message(self.bot, interaction.user.id, interaction.user.display_name),
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

    @app_commands.command(name="擂台", description="查看当前公共擂台。")
    async def arena(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_arena_message(self.bot, interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )

    @app_commands.command(name="开擂", description="押上器魂，成为当前公共擂台的擂主。")
    @app_commands.describe(soul="要押上的器魂数量")
    async def open_arena(self, interaction: discord.Interaction, soul: app_commands.Range[int, 1]) -> None:
        embed, view, broadcasts, success, public_embed = await build_open_arena_message(
            self.bot, interaction.user.id, interaction.user.display_name, soul,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        for content in broadcasts:
            await self.bot.broadcast_service.broadcast(self.bot, content)
        if public_embed is not None:
            await self.bot.broadcast_service.broadcast_embed(self.bot, public_embed)

    @app_commands.command(name="攻擂", description="按当前擂台的等额押注挑战擂主。")
    async def challenge_arena(self, interaction: discord.Interaction) -> None:
        embed, view, broadcasts, success, battle_data = await build_arena_challenge_message(
            self.bot, interaction.user.id, interaction.user.display_name,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        for content in broadcasts:
            await self.bot.broadcast_service.broadcast(self.bot, content)
        if battle_data is not None and interaction.channel is not None:
            await send_public_battle_animation(
                self.bot, interaction.channel,
                battle_data["challenger_snapshot"], battle_data["defender_snapshot"],
                battle_data["battle"],
                mode="arena", summary_lines=battle_data["summary_lines"],
            )

    @app_commands.command(name="收擂", description="当前擂主带走全部擂池并离场。")
    async def claim_arena(self, interaction: discord.Interaction) -> None:
        embed, view, broadcasts, success, public_embed = await build_arena_claim_message(
            self.bot, interaction.user.id, interaction.user.display_name,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        for content in broadcasts:
            await self.bot.broadcast_service.broadcast(self.bot, content)
        if public_embed is not None:
            await self.bot.broadcast_service.broadcast_embed(self.bot, public_embed)

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

    @app_commands.command(name="切磋", description="向其他修士发起一场无奖励的切磋。")
    @app_commands.describe(user="要切磋的道友")
    async def spar(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if user.bot:
            await interaction.response.send_message("不能向灵傀发起切磋。", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("你还不至于自己和自己切磋。", ephemeral=True)
            return
        if not self.bot.pvp_service.reserve_spar_request(interaction.user.id, user.id):
            await interaction.response.send_message("你或对方已有待回应的切磋邀请，请先等当前邀请结束。", ephemeral=True)
            return

        try:
            embed, view, broadcasts, content = await build_spar_request_message(
                self.bot,
                interaction.user.id,
                interaction.user.display_name,
                user.id,
                user.display_name,
            )
            if view is None:
                self.bot.pvp_service.release_spar_request(interaction.user.id, user.id)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            await interaction.response.send_message(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            message = await interaction.original_response()
            view.bind_message(message)
            for broadcast in broadcasts:
                await self.bot.broadcast_service.broadcast(self.bot, broadcast)
        except Exception:
            self.bot.pvp_service.release_spar_request(interaction.user.id, user.id)
            raise

    @app_commands.command(name="轮回", description="舍弃当前主线进度，轮回重修。")
    async def reincarnate(self, interaction: discord.Interaction) -> None:
        await self._send_with_broadcasts(
            interaction,
            await build_reincarnation_confirm_message(self.bot, interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )


async def setup(bot: XianBot) -> None:
    await bot.add_cog(XianCommands(bot))
