from __future__ import annotations

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.sect_service import ResourceSiteView, SectOverview, SectSummary
from bot.utils.formatters import format_big_number, format_qi


def build_sect_overview_embed(
    snapshot: CharacterSnapshot,
    *,
    overview: SectOverview | None,
    joinable_sects: list[SectSummary],
    leave_cooldown_text: str | None = None,
    settlement_lines: list[str] | None = None,
    message: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    description = message
    if description is None:
        description = "山门来往，人心聚散，眼前所立，便是你的归处。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 宗门",
        description=description,
        color=color or discord.Color.dark_teal(),
    )
    if overview is None:
        lines = [
            f"灵石：`{snapshot.lingshi}`",
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`",
        ]
        if leave_cooldown_text:
            lines.append(f"离宗余波：`{leave_cooldown_text}`")
        embed.add_field(name="当前行迹", value="\n".join(lines), inline=False)
        embed.add_field(name="可投宗门", value=_render_joinable_sects(joinable_sects), inline=False)
        return embed

    sect_lines = [
        f"宗门：**{overview.name}**",
        f"阵营：`{overview.faction_name}`",
        f"身份：`{overview.role_name}`",
        f"门人：`{overview.member_count}`",
        f"灵石：`{snapshot.lingshi}`",
        f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`",
        f"今日功绩：`{overview_today_contribution(snapshot)}`",
    ]
    embed.add_field(name="宗门牌面", value="\n".join(sect_lines), inline=False)
    owned_value = "、".join(overview.owned_site_names) if overview.owned_site_names else "今朝尚未握得地脉。"
    embed.add_field(name="门下地脉", value=owned_value, inline=False)
    if settlement_lines:
        embed.add_field(name="昨夜分润", value="\n".join(settlement_lines), inline=False)
    return embed


def build_sect_directory_embed(snapshot: CharacterSnapshot, sects: list[SectSummary], *, message: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 宗门名录",
        description=message or "群峰并立，各门旗号昭昭，择一处落脚即可。",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="可投宗门", value=_render_joinable_sects(sects), inline=False)
    return embed


def build_site_board_embed(
    snapshot: CharacterSnapshot,
    overview: SectOverview,
    sites: list[ResourceSiteView],
    *,
    selected_site: ResourceSiteView | None = None,
    message: str | None = None,
    action_lines: list[str] | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    description = message or "诸宗争地，昼夜不休，落子之处，便是门中今日所争。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 资源争夺",
        description=description,
        color=color or discord.Color.dark_green(),
    )
    embed.add_field(
        name="当前门势",
        value=(
            f"宗门：**{overview.name}**\n"
            f"身份：`{overview.role_name}`\n"
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`\n"
            f"灵石：`{snapshot.lingshi}`"
        ),
        inline=False,
    )
    embed.add_field(name="地脉名录", value=_render_sites(sites), inline=False)
    if selected_site is not None:
        embed.add_field(
            name="当前选中",
            value=f"**{selected_site.site_name}** · `{selected_site.site_type_name}` · 现归 **{selected_site.owner_name}**",
            inline=False,
        )
    if action_lines:
        embed.add_field(name="本次动作", value="\n".join(action_lines), inline=False)
    return embed


def _render_joinable_sects(sects: list[SectSummary]) -> str:
    if not sects:
        return "眼下未见可投之门。"
    return "\n".join(
        f"**{sect.name}** · `{sect.faction_name}` · 门人 {sect.member_count} · 地脉 {sect.owner_site_count}"
        for sect in sects[:10]
    )


def _render_sites(sites: list[ResourceSiteView]) -> str:
    return "\n".join(f"**{site.site_name}** · `{site.site_type_name}` · {site.owner_name}" for site in sites)


def overview_today_contribution(snapshot: CharacterSnapshot) -> str:
    return format_big_number(getattr(snapshot, "sect_contribution_daily", 0))
