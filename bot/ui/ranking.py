from __future__ import annotations

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.ranking_service import LeaderboardResult


def build_leaderboard_embed(result: LeaderboardResult, viewer: CharacterSnapshot | None = None) -> discord.Embed:
    embed = discord.Embed(title=result.title, description=result.subtitle, color=discord.Color.teal())
    if viewer is not None:
        embed.add_field(
            name="你的状态",
            value=f"论道 `#{viewer.current_ladder_rank}` · 剩余挑战 `{viewer.daily_pvp_attempts_left}` 次",
            inline=False,
        )
    lines = [f"`#{entry.rank:>2}` **{entry.player_name}** · {entry.primary_text} · {entry.secondary_text}" for entry in result.entries]
    embed.add_field(name="前列名录", value="\n".join(lines) if lines else "暂无修士入榜。", inline=False)
    return embed
