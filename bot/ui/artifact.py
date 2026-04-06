from __future__ import annotations

import discord

from bot.services.artifact_service import ArtifactPanelState, ReinforceResult
from bot.services.character_service import CharacterSnapshot
from bot.utils.formatters import format_big_number


def build_artifact_embed(snapshot: CharacterSnapshot, panel_state: ArtifactPanelState) -> discord.Embed:
    return _build_artifact_embed(
        snapshot,
        panel_state,
        title=f"{snapshot.player_name} · 本命法宝",
        message=None,
        color=discord.Color.dark_gold(),
    )


def build_artifact_action_embed(
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    title: str,
    message: str,
    color: discord.Color,
    action_title: str | None = None,
    action_lines: list[str] | None = None,
) -> discord.Embed:
    return _build_artifact_embed(
        snapshot,
        panel_state,
        title=title,
        message=message,
        color=color,
        action_title=action_title,
        action_lines=action_lines or [],
    )


def build_reinforce_embed(snapshot: CharacterSnapshot, panel_state: ArtifactPanelState, result: ReinforceResult) -> discord.Embed:
    action_lines = [
        f"强化：`+{result.level_before} -> +{result.level_after}`",
        f"器魂消耗：`{result.soul_cost}`",
        f"成功率：`{int(result.success_rate * 100) if result.success_rate else 0}%`",
    ]
    if result.success:
        action_lines.append(f"成长分配：杀伐 +`{result.gained_atk}` · 护体 +`{result.gained_def}` · 身法 +`{result.gained_agi}`")
    if result.newly_unlocked_slots:
        action_lines.append(f"新解锁槽位：{'、'.join(f'槽{slot}' for slot in result.newly_unlocked_slots)}")
    return _build_artifact_embed(
        snapshot,
        panel_state,
        title=f"{snapshot.player_name} · 锻宝",
        message=result.message,
        color=discord.Color.green() if result.success else discord.Color.orange(),
        action_title="本次锻宝",
        action_lines=action_lines,
    )


def _build_artifact_embed(
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    title: str,
    message: str | None,
    color: discord.Color,
    action_title: str | None = None,
    action_lines: list[str] | None = None,
) -> discord.Embed:
    description = f"**{snapshot.artifact_name}** `+{snapshot.artifact_level}`"
    if message:
        description = f"{description}\n{message}"
    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(
        name="法宝详情",
        value=(
            f"器魂：`{snapshot.soul_shards}`\n"
            f"总成长：`{format_big_number(snapshot.artifact_power)}`\n"
            f"当前尊号：**{snapshot.title}**\n"
            f"已解锁槽位：`{panel_state.unlocked_slots} / 5`"
        ),
        inline=False,
    )
    embed.add_field(name="当前词条", value=_render_affix_column(panel_state.current_slots), inline=True)
    embed.add_field(name="待选词条", value=_render_affix_column(panel_state.pending_slots), inline=True)
    if action_title and action_lines:
        embed.add_field(name=action_title, value="\n".join(action_lines), inline=False)
    embed.set_footer(text="单次洗炼消耗 2 器魂；右侧待选点击“保存待选”后才会写入当前词条。")
    return embed


def _render_affix_column(slot_views) -> str:
    lines: list[str] = []
    for slot_view in slot_views:
        lines.append(f"槽{slot_view.slot}：{slot_view.name}")
        if slot_view.description:
            lines.append(f"  · {slot_view.description}")
    return "\n".join(lines)
