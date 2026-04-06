from __future__ import annotations

import discord

from bot.services.artifact_service import AFFIX_SLOT_UNLOCK_LEVELS, ArtifactPanelState, ReinforceResult
from bot.services.character_service import CharacterSnapshot
from bot.utils.formatters import format_big_number


def build_artifact_overview_embed(
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    message: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 本命法宝",
        description=_artifact_header(snapshot, message),
        color=color or discord.Color.dark_gold(),
    )
    _add_overview_fields(embed, snapshot, panel_state)
    embed.set_footer(text="从下方进入强化、洗炼或改名子面板。")
    return embed


def build_reinforce_panel_embed(
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    stage_cap: int,
    next_level: int,
    soul_cost: int,
    success_rate: float,
    message: str | None = None,
    color: discord.Color | None = None,
    result: ReinforceResult | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 法宝强化",
        description=_artifact_header(snapshot, message),
        color=color or discord.Color.dark_gold(),
    )
    _add_overview_fields(embed, snapshot, panel_state, include_pending=False)
    if snapshot.artifact_level >= stage_cap:
        preview_lines = [
            f"当前境界上限：`+{stage_cap}`",
            "本命法宝已到当前境界强化上限。",
        ]
    else:
        preview_lines = [
            f"下一重：`+{snapshot.artifact_level} -> +{next_level}`",
            f"器魂消耗：`{soul_cost}`",
            f"成功率：`{int(success_rate * 100)}%`",
            f"当前境界上限：`+{stage_cap}`",
        ]
        if next_level in AFFIX_SLOT_UNLOCK_LEVELS:
            preview_lines.append(f"强化到 `+{next_level}` 时会解锁槽{AFFIX_SLOT_UNLOCK_LEVELS.index(next_level) + 1}")
    embed.add_field(name="强化预览", value="\n".join(preview_lines), inline=False)
    if result is not None:
        action_lines = [
            f"强化：`+{result.level_before} -> +{result.level_after}`",
            f"器魂消耗：`{result.soul_cost}`",
            f"成功率：`{int(result.success_rate * 100) if result.success_rate else 0}%`",
        ]
        if result.success:
            action_lines.append(f"成长分配：杀伐 +`{result.gained_atk}` · 护体 +`{result.gained_def}` · 身法 +`{result.gained_agi}`")
        if result.newly_unlocked_slots:
            action_lines.append(f"新解锁槽位：{'、'.join(f'槽{slot}' for slot in result.newly_unlocked_slots)}")
        embed.add_field(name="本次强化", value="\n".join(action_lines), inline=False)
    embed.set_footer(text="强化达到 +10 / +20 / +30 / +40 / +50 时，会依次解锁词条槽位。")
    return embed


def build_refine_panel_embed(
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    title: str | None = None,
    message: str | None = None,
    color: discord.Color | None = None,
    action_title: str | None = None,
    action_lines: list[str] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title or f"{snapshot.player_name} · 法宝洗炼",
        description=_artifact_header(snapshot, message),
        color=color or discord.Color.dark_gold(),
    )
    _add_refine_fields(embed, snapshot, panel_state)
    embed.add_field(name="当前词条", value=_render_affix_column(panel_state.current_slots), inline=True)
    embed.add_field(name="待选词条", value=_render_affix_column(panel_state.pending_slots), inline=True)
    if action_title and action_lines:
        embed.add_field(name=action_title, value="\n".join(action_lines), inline=False)
    embed.set_footer(text="单次洗炼消耗 2 器魂；待选词条点击“保存待选”后才会写入当前词条。")
    return embed


def _artifact_header(snapshot: CharacterSnapshot, message: str | None) -> str:
    header = f"**{snapshot.artifact_name}** `+{snapshot.artifact_level}`"
    if message:
        return f"{header}\n{message}"
    return header


def _add_overview_fields(
    embed: discord.Embed,
    snapshot: CharacterSnapshot,
    panel_state: ArtifactPanelState,
    *,
    include_pending: bool = True,
) -> None:
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
    embed.add_field(
        name="三维加成",
        value=(
            f"⚔️ 杀伐：`+{format_big_number(snapshot.artifact_atk_bonus)}`\n"
            f"🛡 护体：`+{format_big_number(snapshot.artifact_def_bonus)}`\n"
            f"💨 身法：`+{format_big_number(snapshot.artifact_agi_bonus)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="总三维",
        value=(
            f"⚔️ 杀伐：`{format_big_number(snapshot.total_atk)}`\n"
            f"🛡 护体：`{format_big_number(snapshot.total_def)}`\n"
            f"💨 身法：`{format_big_number(snapshot.total_agi)}`"
        ),
        inline=True,
    )
    if include_pending and panel_state.has_pending:
        embed.add_field(name="待保存槽位", value=_render_pending_summary(panel_state), inline=False)


def _add_refine_fields(embed: discord.Embed, snapshot: CharacterSnapshot, panel_state: ArtifactPanelState) -> None:
    embed.add_field(
        name="洗炼信息",
        value=(
            f"器魂：`{snapshot.soul_shards}`\n"
            f"已解锁槽位：`{panel_state.unlocked_slots} / 5`\n"
            f"待选结果：`{sum(1 for slot in panel_state.pending_slots if slot.affix_id)} / {panel_state.unlocked_slots}`"
        ),
        inline=False,
    )
    if panel_state.has_pending:
        embed.add_field(name="待保存槽位", value=_render_pending_summary(panel_state), inline=False)


def _render_pending_summary(panel_state: ArtifactPanelState) -> str:
    lines = [f"槽{slot.slot}：{slot.name}" for slot in panel_state.pending_slots if slot.affix_id]
    return "\n".join(lines) if lines else "当前没有待保存词条。"


def _render_affix_column(slot_views) -> str:
    lines: list[str] = []
    for slot_view in slot_views:
        lines.append(f"槽{slot_view.slot}：{slot_view.name}")
        if slot_view.description:
            lines.append(f"  · {slot_view.description}")
    return "\n".join(lines)
