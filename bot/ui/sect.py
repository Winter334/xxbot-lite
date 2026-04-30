from __future__ import annotations

from datetime import date
from typing import Iterable

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.sect_service import ResourceSiteView, SectMemberView, SectOverview, SectSummary, SectTaskBoard, SectTaskView
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
    task_summary_text: str | None = None,
) -> discord.Embed:
    description = message
    if description is None:
        description = "查看宗门成员、资源点和最近结算情况。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 宗门",
        description=description,
        color=color or discord.Color.dark_teal(),
    )
    if overview is None:
        lines = [
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`",
        ]
        if leave_cooldown_text:
            lines.append(f"离宗余波：`{leave_cooldown_text}`")
        embed.add_field(name="当前状态", value="\n".join(lines), inline=False)
        embed.add_field(name="可投宗门", value=_render_joinable_sects(joinable_sects), inline=False)
        return embed

    sect_lines = [
        f"宗门：**{overview.name}**",
        f"阵营：`{overview.faction_name}`",
        f"身份：`{overview.role_name}`",
        f"门人：`{overview.member_count}`",
        f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`",
        f"今日贡献：`{overview_today_contribution(snapshot)}`",
    ]
    if task_summary_text:
        sect_lines.append(f"宗门任务：`{task_summary_text}`")
    embed.add_field(name="宗门信息", value="\n".join(sect_lines), inline=False)
    owned_value = "、".join(overview.owned_site_names) if overview.owned_site_names else "当前没有占领资源点。"
    embed.add_field(name="已占资源点", value=owned_value, inline=False)
    embed.add_field(name="成员列表", value=_render_members(overview.members), inline=False)
    focus_member = selected_member or (overview.members[0] if overview.members else None)
    if focus_member is not None:
        embed.add_field(name="成员详情", value=_render_member_detail(focus_member), inline=False)
    reward_text = "\n".join(settlement_lines) if settlement_lines else snapshot.sect_last_settlement_summary
    if reward_text:
        embed.add_field(
            name=_format_settlement_label(snapshot.sect_last_settlement_on),
            value=reward_text,
            inline=False,
        )
    return embed


def build_sect_help_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 资源点说明",
        description="查看资源点的刷新、占领和结算规则。",
        color=discord.Color.dark_gold(),
    )
    embed.add_field(
        name="刷新",
        value="每天会刷新新的资源点。\n每个资源点最多存在 `3` 日。",
        inline=False,
    )
    embed.add_field(
        name="操作",
        value="同一时间只能绑定 `1` 个资源点岗位。\n可执行 `强争`、`护持`、`输运`。",
        inline=False,
    )
    embed.add_field(
        name="占领",
        value="无主资源点需要 `2` 次强争。\n已有归属的资源点需要推进到 `4` 次。",
        inline=False,
    )
    embed.add_field(
        name="输运",
        value="每名输运成员可让该资源点结算奖励提高 `10%`。",
        inline=False,
    )
    return embed


def build_sect_directory_embed(snapshot: CharacterSnapshot, sects: list[SectSummary], *, message: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 宗门名录",
        description=message or "查看当前可加入的宗门。",
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
    description = message or "查看当前资源点和可执行操作。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 资源争夺",
        description=description,
        color=color or discord.Color.dark_green(),
    )
    embed.add_field(
        name="当前状态",
        value=(
            f"宗门：**{overview.name}**\n"
            f"身份：`{overview.role_name}`\n"
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`"
        ),
        inline=False,
    )
    embed.add_field(name="资源点列表", value=_render_sites(sites), inline=False)
    if selected_site is not None:
        detail_lines = [
            f"归属宗门：**{selected_site.owner_name}**",
            f"剩余时间：`{selected_site.days_left}` 日",
        ]
        if selected_site.guard_count > 0 or selected_site.owner_name != "无主":
            detail_lines.append(f"护持人数：`{selected_site.guard_count}`")
        if selected_site.transport_count > 0:
            detail_lines.append(f"输运人数：`{selected_site.transport_count}`")
        if selected_site.player_role_name:
            detail_lines.append(f"你的岗位：`{selected_site.player_role_name}`")
        if selected_site.player_progress_required > 0:
            detail_lines.append(f"本宗进度：`{selected_site.player_progress}/{selected_site.player_progress_required}`")
        if selected_site.attack_summaries:
            detail_lines.append(f"其他宗门：{' · '.join(selected_site.attack_summaries)}")
        embed.add_field(
            name="资源点详情",
            value=f"**{selected_site.site_name}** · `{selected_site.site_type_name}`\n" + "\n".join(detail_lines),
            inline=False,
        )
    if action_lines:
        embed.add_field(name="操作结果", value="\n".join(action_lines), inline=False)
    return embed


def build_sect_task_board_embed(
    snapshot: CharacterSnapshot,
    overview: SectOverview,
    task_board: SectTaskBoard,
    *,
    selected_task: SectTaskView | None = None,
    message: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    description = message or "领取宗门任务，完成后可在此领奖。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 宗门任务",
        description=description,
        color=color or discord.Color.dark_magenta(),
    )
    embed.add_field(
        name="当前状态",
        value=(
            f"宗门：**{overview.name}**\n"
            f"身份：`{overview.role_name}`\n"
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`\n"
            f"任务概览：`{_render_task_summary(task_board)}`"
        ),
        inline=False,
    )
    embed.add_field(name="今日任务", value=_render_task_list(task_board.tasks), inline=False)
    focus_task = selected_task or (task_board.tasks[0] if task_board.tasks else None)
    if focus_task is not None:
        embed.add_field(name="任务详情", value=_render_task_detail(focus_task), inline=False)
    return embed


def _render_joinable_sects(sects: list[SectSummary]) -> str:
    if not sects:
        return "当前没有可加入的宗门。"
    return "\n".join(
        f"**{sect.name}** · `{sect.faction_name}` · 门人 {sect.member_count} · 资源点 {sect.owner_site_count}"
        for sect in sects[:10]
    )


def _render_sites(sites: list[ResourceSiteView]) -> str:
    if not sites:
        return "当前没有可争夺的资源点。"
    return "\n".join(
        f"**{site.site_name}** · `{site.site_type_name}` · {site.owner_name} · 剩余 `{site.days_left}` 日"
        for site in sites
    )


def overview_today_contribution(snapshot: CharacterSnapshot) -> str:
    return format_big_number(getattr(snapshot, "sect_contribution_daily", 0))


def _render_task_summary(task_board: SectTaskBoard) -> str:
    parts: list[str] = []
    if task_board.available_count > 0:
        parts.append(f"可领取 {task_board.available_count}")
    if task_board.active_count > 0:
        parts.append(f"进行中 {task_board.active_count}")
    if task_board.completed_count > 0:
        parts.append(f"可领奖 {task_board.completed_count}")
    if task_board.claimed_count > 0:
        parts.append(f"已领完 {task_board.claimed_count}")
    return " · ".join(parts) if parts else "今日暂无任务"


def _render_members(members: tuple[SectMemberView, ...]) -> str:
    if not members:
        return "当前没有成员。"
    return "\n".join(
        f"{index}. **{member.display_name}** · `{member.role_name}` · {member.realm_display}"
        for index, member in enumerate(members[:10], start=1)
    )


def _render_member_detail(member: SectMemberView) -> str:
    return (
        f"道号：**{member.display_name}**\n"
        f"职司：`{member.role_name}`\n"
        f"境界：`{member.realm_display}`\n"
        f"今日贡献：`{format_big_number(member.contribution_daily)}`\n"
        f"本周贡献：`{format_big_number(member.contribution_weekly)}`\n"
        f"累计贡献：`{format_big_number(member.contribution_total)}`"
    )


def _render_task_list(tasks: Iterable[SectTaskView]) -> str:
    lines = []
    for index, task in enumerate(tasks, start=1):
        lines.append(f"{index}. **{task.title}** · `{task.status_name}` · {task.progress}/{task.target}")
    return "\n".join(lines) if lines else "今日暂无可领任务。"


def _render_task_detail(task: SectTaskView) -> str:
    return (
        f"任务：**{task.title}**\n"
        f"状态：`{task.status_name}`\n"
        f"进度：`{task.progress}/{task.target}`\n"
        f"说明：{task.description}\n"
        f"奖励：{_render_task_reward(task)}"
    )


def _render_task_reward(task: SectTaskView) -> str:
    parts = []
    if task.reward_lingshi > 0:
        parts.append(f"灵石 +{task.reward_lingshi}")
    if task.reward_soul > 0:
        parts.append(f"器魂 +{task.reward_soul}")
    if task.reward_luck > 0:
        parts.append(f"气运 +{task.reward_luck}")
    return " · ".join(parts) if parts else "无"


def _format_settlement_label(settlement_day: date | None) -> str:
    if settlement_day is None:
        return "最近一次资源点结算"
    return f"{settlement_day.month}月{settlement_day.day}日资源点结算"
