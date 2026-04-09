from __future__ import annotations

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.ranking_service import LeaderboardEntry, LeaderboardResult
from bot.utils.formatters import format_big_number


CATEGORY_STYLE = {
    "ladder": {
        "icon": "⚔️",
        "color": discord.Color.red(),
        "viewer_title": "🪪 你的论位",
        "top_title": "👑 前三论席",
        "rest_title": "📜 榜上诸修",
        "footer": "胜者取位，败者守位。",
    },
    "power": {
        "icon": "⚡",
        "color": discord.Color.gold(),
        "viewer_title": "🪪 你的牌面",
        "top_title": "👑 战力绝峰",
        "rest_title": "📜 战力前列",
        "footer": "战力只看牌面，不直接决定胜负。",
    },
    "realm_power": {
        "icon": "🪞",
        "color": discord.Color.blue(),
        "viewer_title": "🪪 你的同境位置",
        "top_title": "👑 同境前三",
        "rest_title": "📜 同境前列",
        "footer": "同境争锋，更看法宝与命格。",
    },
    "tower": {
        "icon": "🗼",
        "color": discord.Color.blurple(),
        "viewer_title": "🪪 你的塔势",
        "top_title": "👑 登塔绝顶",
        "rest_title": "📜 登塔名录",
        "footer": "只看最高已踏破层数。",
    },
    "artifact": {
        "icon": "🛠️",
        "color": discord.Color.dark_gold(),
        "viewer_title": "🪪 你的本命",
        "top_title": "👑 神兵前列",
        "rest_title": "📜 本命名录",
        "footer": "器魂所聚，本命分高下。",
    },
    "realm": {
        "icon": "🌌",
        "color": discord.Color.purple(),
        "viewer_title": "🪪 你的境位",
        "top_title": "👑 境界前列",
        "rest_title": "📜 境界序列",
        "footer": "境界与当前修为共同排序。",
    },
    "righteous": {
        "icon": "☀️",
        "color": discord.Color.gold(),
        "viewer_title": "🪪 你的善名",
        "top_title": "👑 正道前列",
        "rest_title": "📜 正道群英",
        "footer": "善名愈盛，正道声望愈高。",
    },
    "demonic": {
        "icon": "🌘",
        "color": discord.Color.dark_red(),
        "viewer_title": "🪪 你的恶名",
        "top_title": "👑 魔道前列",
        "rest_title": "📜 魔道群魔",
        "footer": "恶名越重，头上赏格也越高。",
    },
    "bounty": {
        "icon": "📜",
        "color": discord.Color.orange(),
        "viewer_title": "🪪 你的赏格",
        "top_title": "👑 赏格最高",
        "rest_title": "📜 悬赏名录",
        "footer": "此榜只列当前赏格最高者。",
    },
}


def _rank_badge(rank: int) -> str:
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return f"`#{rank:>2}`"


def _render_top_entry(entry: LeaderboardEntry) -> str:
    badge = _rank_badge(entry.rank)
    return f"{badge} **{entry.player_name}**\n└ {entry.primary_text} · {entry.secondary_text}"


def _render_compact_entry(entry: LeaderboardEntry) -> str:
    badge = _rank_badge(entry.rank)
    return f"{badge} **{entry.player_name}** · {entry.primary_text} · {entry.secondary_text}"


def build_leaderboard_embed(result: LeaderboardResult, viewer: CharacterSnapshot | None = None) -> discord.Embed:
    style = CATEGORY_STYLE.get(result.category, CATEGORY_STYLE["ladder"])
    embed = discord.Embed(
        title=f"{style['icon']} {result.title}",
        description=result.subtitle,
        color=style["color"],
    )
    if viewer is not None:
        viewer_lines = [
            f"境界：`{viewer.realm_display}`",
            f"称号：**{viewer.title}**",
            f"战力：`{format_big_number(viewer.combat_power)}`",
        ]
        if result.category == "ladder":
            viewer_lines.insert(0, f"论道：`#{viewer.current_ladder_rank}`")
            viewer_lines.append(f"剩余挑战：`{viewer.daily_pvp_attempts_left}` 次")
        elif result.category == "tower":
            viewer_lines.append(f"最高塔层：`{viewer.historical_highest_floor}` 层")
        elif result.category == "artifact":
            viewer_lines.append(f"本命：**{viewer.artifact_name}** `+{viewer.artifact_level}`")
        elif result.category == "realm":
            viewer_lines.append(f"当前修为：`{format_big_number(viewer.cultivation)}`")
        elif result.category == "righteous":
            viewer_lines = [
                f"阵营：`{viewer.faction_name}`",
                f"善名：`{viewer.virtue}`",
                f"气运：`{viewer.luck}`",
            ]
        elif result.category == "demonic":
            viewer_lines = [
                f"阵营：`{viewer.faction_name}`",
                f"恶名：`{viewer.infamy}`",
                f"悬赏：`{viewer.bounty_soul}`",
            ]
        elif result.category == "bounty":
            viewer_lines = [
                f"阵营：`{viewer.faction_name}`",
                f"当前赏格：`{viewer.bounty_soul}`",
                f"恶名：`{viewer.infamy}`",
            ]
        embed.add_field(name=style["viewer_title"], value="\n".join(viewer_lines), inline=False)

    top_entries = result.entries[:3]
    rest_entries = result.entries[3:10]
    if top_entries:
        embed.add_field(
            name=style["top_title"],
            value="\n\n".join(_render_top_entry(entry) for entry in top_entries),
            inline=False,
        )
    if rest_entries:
        embed.add_field(
            name=style["rest_title"],
            value="\n".join(_render_compact_entry(entry) for entry in rest_entries),
            inline=False,
        )
    if not result.entries:
        embed.add_field(name=style["rest_title"], value="暂无修士入榜。", inline=False)
    embed.set_footer(text=style["footer"])
    return embed
