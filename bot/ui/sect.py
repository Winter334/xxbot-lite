from __future__ import annotations

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.sect_service import ResourceSiteView, SectMemberView, SectOverview, SectSummary
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
    selected_member: SectMemberView | None = None,
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
    embed.add_field(name="门人录", value=_render_members(overview.members), inline=False)
    focus_member = selected_member or (overview.members[0] if overview.members else None)
    if focus_member is not None:
        embed.add_field(name="当前所观", value=_render_member_detail(focus_member), inline=False)
    if settlement_lines:
        embed.add_field(name="昨夜分润", value="\n".join(settlement_lines), inline=False)
    return embed


def build_sect_help_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 地脉札记",
        description="山门争地，只看眼前这几步落子。",
        color=discord.Color.dark_gold(),
    )
    embed.add_field(
        name="寻脉",
        value="每日会有新地脉现身。\n每处地脉最多留世 `3` 日。",
        inline=False,
    )
    embed.add_field(
        name="落子",
        value="一人同一时刻只可守 `1` 处地脉。\n强争夺旗，护持守门，输运养脉。",
        inline=False,
    )
    embed.add_field(
        name="夺脉",
        value="无主地脉连下 `2` 手即可立旗。\n已有旗主的地脉，要连破 `4` 阵。",
        inline=False,
    )
    embed.add_field(
        name="输运",
        value="每名输运门人，让该地脉分润再涨 `10%`。",
        inline=False,
    )
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
    description = message or "诸宗旗号都压在地脉上，你这一手，会把门中旗子落在哪里。"
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
        detail_lines = [
            f"旗主：**{selected_site.owner_name}**",
            f"灵机：`还余 {selected_site.days_left} 日`",
        ]
        if selected_site.guard_count > 0 or selected_site.owner_name != "无主":
            detail_lines.append(f"护持：`{selected_site.guard_count}`人")
        if selected_site.transport_count > 0:
            detail_lines.append(f"输运：`{selected_site.transport_count}`人")
        if selected_site.player_role_name:
            detail_lines.append(f"你在此地：`{selected_site.player_role_name}`")
        if selected_site.player_progress_required > 0:
            detail_lines.append(f"本门占势：`{selected_site.player_progress}/{selected_site.player_progress_required}`")
        if selected_site.attack_summaries:
            detail_lines.append(f"来犯：{' · '.join(selected_site.attack_summaries)}")
        embed.add_field(
            name="当前选中",
            value=f"**{selected_site.site_name}** · `{selected_site.site_type_name}`\n" + "\n".join(detail_lines),
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
    if not sites:
        return "这一刻四野沉静，暂未见可争之脉。"
    return "\n".join(
        f"**{site.site_name}** · `{site.site_type_name}` · {site.owner_name} · 余 `{site.days_left}` 日"
        for site in sites
    )


def overview_today_contribution(snapshot: CharacterSnapshot) -> str:
    return format_big_number(getattr(snapshot, "sect_contribution_daily", 0))


def _render_members(members: tuple[SectMemberView, ...]) -> str:
    if not members:
        return "山门冷清，暂未见门人踪影。"
    return "\n".join(
        f"{index}. **{member.display_name}** · `{member.role_name}` · {member.realm_display}"
        for index, member in enumerate(members[:10], start=1)
    )


def _render_member_detail(member: SectMemberView) -> str:
    return (
        f"道号：**{member.display_name}**\n"
        f"职司：`{member.role_name}`\n"
        f"境界：`{member.realm_display}`\n"
        f"今日功绩：`{format_big_number(member.contribution_daily)}`\n"
        f"本周功绩：`{format_big_number(member.contribution_weekly)}`\n"
        f"累计功绩：`{format_big_number(member.contribution_total)}`"
    )
