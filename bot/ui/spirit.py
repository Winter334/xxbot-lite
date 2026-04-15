from __future__ import annotations

import discord

from bot.services.character_service import CharacterSnapshot
from bot.services.spirit_service import SpiritPanelState, SpiritView
from bot.utils.formatters import format_big_number


def build_spirit_panel_embed(
    snapshot: CharacterSnapshot,
    panel_state: SpiritPanelState,
    *,
    message: str | None = None,
    color: discord.Color | None = None,
    action_title: str | None = None,
    action_lines: list[str] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 器灵",
        description=message or panel_state.action_text,
        color=color or discord.Color.dark_teal(),
    )
    if not panel_state.unlocked:
        embed.add_field(
            name="当前情况",
            value=f"本命法宝需达 `+60`，方可孕育器灵。\n当前强化：`+{snapshot.artifact_level}`",
            inline=False,
        )
        return embed

    embed.add_field(name="灵炉动静", value=panel_state.action_text, inline=False)
    embed.add_field(name="当前器灵", value=_render_spirit(panel_state.current_spirit), inline=True)
    if panel_state.pending_spirit is not None:
        embed.add_field(name="待选新灵相", value=_render_spirit(panel_state.pending_spirit), inline=True)
    if action_title and action_lines:
        embed.add_field(name=action_title, value="\n".join(action_lines), inline=False)
    return embed


def _render_spirit(spirit: SpiritView | None) -> str:
    if spirit is None:
        return "尚未见器灵。"
    stat_lines = []
    for entry in spirit.stats:
        stat_lines.append(f"{entry.label}：`{entry.kind_name} {entry.value_text}` · 当前生效 `+{format_big_number(entry.effective_bonus)}`")
    return "\n".join(
        [
            f"器灵：**{spirit.name}**",
            f"品阶：`{spirit.tier_name}`",
            f"神通：**{spirit.power_name}**",
            *stat_lines,
            spirit.power_description,
        ]
    )
