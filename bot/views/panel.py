from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from bot.ui.artifact import (
    build_artifact_overview_embed,
    build_refine_panel_embed,
    build_reinforce_panel_embed,
)
from bot.ui.panel import (
    build_breakthrough_embed,
    build_faction_action_embed,
    build_faction_embed,
    build_fate_rewrite_confirm_embed,
    build_fate_rewrite_embed,
    build_ladder_battle_embed,
    build_ladder_round_embed,
    build_panel_embed,
    build_reincarnation_confirm_embed,
    build_reincarnation_embed,
    build_retreat_embed,
    build_retreat_settlement_embed,
    build_tower_embed,
    build_tower_floor_embed,
    build_travel_embed,
    build_travel_settlement_embed,
)
from bot.ui.ranking import build_leaderboard_embed
from bot.services.faction_service import FactionTarget
from bot.services.travel_service import TRAVEL_DURATION_CHOICES

if TYPE_CHECKING:
    from bot.main import XianBot


def _info_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.orange())


async def _refresh_resources(bot: XianBot, character) -> int:
    return bot.idle_service.recover_qi(character)


async def _send_broadcasts(bot: XianBot, broadcasts: list[str]) -> None:
    for content in broadcasts:
        await bot.broadcast_service.broadcast(bot, content)


async def _sync_snapshot(bot: XianBot, session, character) -> tuple:
    bot.ladder_service.reset_daily_attempts_if_needed(character)
    await _refresh_resources(bot, character)
    bot.faction_service.sync_character_state(character)
    bot.character_service.refresh_combat_power(character)
    title, honor_tags, faction_title = await bot.ranking_service.get_titles(session, character)
    character.title = title
    snapshot = bot.character_service.build_snapshot(
        character,
        title=title,
        faction_title=faction_title,
        honor_tags=honor_tags,
        idle_minutes=bot.idle_service.current_idle_minutes(character),
        travel_minutes=bot.travel_service.current_travel_minutes(character),
    )
    return snapshot


async def _load_active_character(bot: XianBot, user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return character.id, snapshot, broadcasts


async def build_panel_message(
    bot: XianBot,
    owner_user_id: int,
    display_name: str,
    *,
    avatar_url: str | None = None,
    target_user_id: int | None = None,
    target_avatar_url: str | None = None,
) -> tuple[discord.Embed, discord.ui.View | None, list[str]]:
    if target_user_id is not None and target_user_id != owner_user_id:
        async with bot.session_factory() as session:
            character = await bot.character_service.get_character_by_discord_id(session, target_user_id)
            if character is None:
                return _info_embed("未见道友", "此人尚未踏入仙途。"), None, []
            snapshot = await _sync_snapshot(bot, session, character)
            await session.commit()
        return build_panel_embed(snapshot, avatar_url=target_avatar_url), None, []

    _, snapshot, broadcasts = await _load_active_character(bot, owner_user_id, display_name)
    return build_panel_embed(snapshot, avatar_url=avatar_url), PanelView(owner_user_id), broadcasts


async def build_leaderboard_message(
    bot: XianBot,
    owner_user_id: int,
    display_name: str,
    *,
    category: str = "ladder",
) -> tuple[discord.Embed, discord.ui.View, list[str]]:
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        viewer = creation.character
        viewer_snapshot = await _sync_snapshot(bot, session, viewer)
        leaderboard = await bot.ranking_service.build_leaderboard(session, category, viewer)
        targets = await bot.ladder_service.get_challenge_targets(session, viewer) if category == "ladder" else []
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_leaderboard_embed(leaderboard, viewer_snapshot), LeaderboardView(owner_user_id, category, targets), broadcasts


async def _load_tower_run(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        result = bot.tower_service.run_tower(character)
        snapshot = await _sync_snapshot(bot, session, character)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.highest_floor_after > result.highest_floor_before and result.highest_floor_after % 25 == 0:
            broadcasts.append(f"【塔影留名】{snapshot.player_name} 已踏破通天塔第 {result.highest_floor_after} 层。")
        await session.commit()
    return snapshot, result, broadcasts, None


async def run_private_tower_sequence(
    bot: XianBot,
    interaction: discord.Interaction,
    *,
    owner_user_id: int,
    display_name: str,
    edit_existing: bool = False,
) -> None:
    snapshot, result, broadcasts, idle_notice = await _load_tower_run(bot, owner_user_id, display_name)

    if not result.floors:
        blocked = (snapshot.is_retreating or snapshot.is_traveling) and not result.success
        embed = _info_embed("当前无法登塔", result.message) if blocked else build_tower_embed(snapshot, result, idle_notice=idle_notice)
        view = None if blocked else TowerRunView(owner_user_id, can_retry=False)
        if edit_existing:
            await interaction.response.defer()
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)
        return

    preview_embed = build_tower_floor_embed(snapshot, result.floors[0], preview=True, idle_notice=idle_notice)
    if edit_existing:
        await interaction.response.defer()
        await interaction.edit_original_response(embed=preview_embed, view=None)
    else:
        await interaction.response.send_message(embed=preview_embed, ephemeral=True)

    await asyncio.sleep(1.5)
    for index, floor_result in enumerate(result.floors):
        is_last = index == len(result.floors) - 1
        resolved_embed = build_tower_floor_embed(
            snapshot,
            floor_result,
            preview=False,
            run_result=result if is_last else None,
            idle_notice=idle_notice if is_last else None,
        )
        resolved_view = TowerRunView(owner_user_id, can_retry=result.qi_after > 0) if is_last else None
        await interaction.edit_original_response(embed=resolved_embed, view=resolved_view)
        if not is_last:
            await asyncio.sleep(1.8)
            next_preview = build_tower_floor_embed(snapshot, result.floors[index + 1], preview=True)
            await interaction.edit_original_response(embed=next_preview, view=None)
            await asyncio.sleep(1.5)

    await _send_broadcasts(bot, broadcasts)


async def build_breakthrough_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        result = bot.breakthrough_service.attempt_breakthrough(character)
        snapshot = await _sync_snapshot(bot, session, character)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.success and result.reached_new_realm:
            broadcasts.append(f"【大境将成】{snapshot.player_name} 已踏入 {snapshot.realm_display}。")
        await session.commit()
    return build_breakthrough_embed(snapshot, result, idle_notice=None), None, broadcasts


async def build_artifact_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        bot.artifact_service.ensure_affix_slots(character.artifact)
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_artifact_overview_embed(snapshot, panel_state), ArtifactOverviewView(owner_user_id), broadcasts


async def build_reinforce_panel_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        stage = bot.character_service.get_stage(character)
        next_level = min(character.artifact.reinforce_level + 1, stage.reinforce_cap)
        soul_cost = bot.artifact_service.reinforce_cost(next_level) if character.artifact.reinforce_level < stage.reinforce_cap else 0
        success_rate = bot.artifact_service.reinforce_success_rate(next_level) if character.artifact.reinforce_level < stage.reinforce_cap else 0.0
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    embed = build_reinforce_panel_embed(
        snapshot,
        panel_state,
        stage_cap=stage.reinforce_cap,
        next_level=next_level,
        soul_cost=soul_cost,
        success_rate=success_rate,
    )
    return embed, ArtifactReinforceView(owner_user_id), broadcasts


async def build_reinforce_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = bot.artifact_service.reinforce(character.artifact, bot.character_service.get_stage(character))
        bot.character_service.refresh_combat_power(character)
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        stage = bot.character_service.get_stage(character)
        next_level = min(character.artifact.reinforce_level + 1, stage.reinforce_cap)
        soul_cost = bot.artifact_service.reinforce_cost(next_level) if character.artifact.reinforce_level < stage.reinforce_cap else 0
        success_rate = bot.artifact_service.reinforce_success_rate(next_level) if character.artifact.reinforce_level < stage.reinforce_cap else 0.0
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    embed = build_reinforce_panel_embed(
        snapshot,
        panel_state,
        stage_cap=stage.reinforce_cap,
        next_level=next_level,
        soul_cost=soul_cost,
        success_rate=success_rate,
        message=result.message,
        color=discord.Color.green() if result.success else discord.Color.orange(),
        result=result,
    )
    return embed, ArtifactReinforceView(owner_user_id), broadcasts


async def build_refine_panel_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_refine_panel_embed(snapshot, panel_state), ArtifactRefineView(owner_user_id, panel_state), broadcasts


async def build_refine_affix_message(bot: XianBot, owner_user_id: int, display_name: str, slot: int):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = bot.artifact_service.refine_affix(character.artifact, slot)
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    action_lines: list[str] = [f"槽位：槽{slot}", f"器魂：`{result.soul_before} -> {result.soul_after}`"]
    if result.success and result.pending_entry is not None:
        action_lines.append(f"待选词条：**{bot.artifact_service.affix_name(result.pending_entry)}**")
        action_lines.append(bot.artifact_service.describe_affix(result.pending_entry))
    embed = build_refine_panel_embed(
        snapshot,
        panel_state,
        title=f"{snapshot.player_name} · 法宝洗炼",
        message=result.message,
        color=discord.Color.green() if result.success else discord.Color.orange(),
        action_title="本次洗炼",
        action_lines=action_lines,
    )
    return embed, ArtifactRefineView(owner_user_id, panel_state), broadcasts


async def build_save_affixes_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = bot.artifact_service.save_pending_affixes(character.artifact)
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    action_lines = [f"写入槽位：{'、'.join(f'槽{slot}' for slot in result.applied_slots) if result.applied_slots else '无'}"]
    embed = build_refine_panel_embed(
        snapshot,
        panel_state,
        title=f"{snapshot.player_name} · 法宝洗炼",
        message=result.message,
        color=discord.Color.green() if result.success else discord.Color.orange(),
        action_title="保存结果",
        action_lines=action_lines,
    )
    return embed, ArtifactRefineView(owner_user_id, panel_state), broadcasts


async def build_retreat_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_retreat_embed(snapshot), RetreatView(owner_user_id, is_retreating=snapshot.is_retreating), broadcasts


async def build_travel_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_travel_embed(snapshot), TravelView(owner_user_id, snapshot=snapshot), broadcasts


async def cycle_travel_duration_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        next_minutes = bot.travel_service.cycle_selected_duration(character)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    embed = build_travel_embed(snapshot)
    embed.description = f"已将本次行程调整为 {next_minutes} 分钟。"
    return embed, TravelView(owner_user_id, snapshot=snapshot), broadcasts


async def start_travel_message(bot: XianBot, owner_user_id: int, display_name: str, duration_minutes: int | None = None):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        result = bot.travel_service.start_travel(character, duration_minutes)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    embed = build_travel_embed(snapshot)
    embed.description = result.message
    return embed, TravelView(owner_user_id, snapshot=snapshot), broadcasts


async def stop_travel_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        settlement = bot.travel_service.stop_travel(character)
        bot.character_service.refresh_combat_power(character)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_travel_settlement_embed(snapshot, settlement), TravelView(owner_user_id, snapshot=snapshot), broadcasts


async def start_retreat_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _refresh_resources(bot, character)
        result = bot.character_service.start_retreat(character)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    embed = build_retreat_embed(snapshot)
    embed.description = result.message
    return embed, RetreatView(owner_user_id, is_retreating=snapshot.is_retreating), broadcasts


async def stop_retreat_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        settlement = bot.idle_service.settle_retreat(character)
        result = bot.character_service.stop_retreat(character, settlement)
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_retreat_settlement_embed(snapshot, settlement, result.message), RetreatView(owner_user_id, is_retreating=snapshot.is_retreating), broadcasts


async def rename_artifact_message(bot: XianBot, owner_user_id: int, display_name: str, new_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = bot.artifact_service.rename_artifact(character.artifact, new_name)
        if result.success:
            character.last_highlight_text = f"方才为本命法宝赐名「{result.name_after}」。"
        snapshot = await _sync_snapshot(bot, session, character)
        panel_state = bot.artifact_service.build_panel_state(character.artifact)
        renamed_used = character.artifact.artifact_rename_used
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    suffix = f"当前本命已改名：`{'是' if renamed_used else '否'}`"
    embed = build_artifact_overview_embed(
        snapshot,
        panel_state,
        message=f"{result.message}\n{suffix}",
        color=discord.Color.green() if result.success else discord.Color.orange(),
    )
    return embed, ArtifactOverviewView(owner_user_id), broadcasts


async def build_reincarnation_confirm_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_reincarnation_confirm_embed(snapshot), ReincarnationConfirmView(owner_user_id), broadcasts


async def build_reincarnation_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = await bot.character_service.reincarnate(session, character)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.success:
            await bot.ladder_service.move_to_bottom(session, character)
        snapshot = await _sync_snapshot(bot, session, character)
        if result.broadcast_text:
            broadcasts.append(result.broadcast_text)
        await session.commit()
    return build_reincarnation_embed(snapshot, result.message), None, broadcasts


async def build_fate_rewrite_confirm_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_fate_rewrite_confirm_embed(snapshot), FateRewriteConfirmView(owner_user_id, can_confirm=snapshot.rewrite_chances > 0), broadcasts


async def build_fate_rewrite_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        result = await bot.character_service.rewrite_fate(session, character)
        snapshot = await _sync_snapshot(bot, session, character)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.broadcast_text:
            broadcasts.append(result.broadcast_text)
        await session.commit()
    return build_fate_rewrite_embed(snapshot, result.message), None, broadcasts


async def build_faction_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character)
        characters = await bot.character_service.list_characters(session)
        bot.faction_service.sync_many(characters)
        if character.faction == "righteous":
            targets = bot.faction_service.list_bounty_targets(characters)
        elif character.faction == "demonic":
            targets = bot.faction_service.list_robbery_targets(characters, character)
        else:
            targets = []
        can_rob, rob_reason = bot.faction_service.can_rob(character)
        can_bounty, bounty_reason = bot.faction_service.can_bounty_hunt(character)
        robbery_status_text = "可出手" if can_rob else (rob_reason or "当前不可出手")
        bounty_status_text = "可出手" if can_bounty else (bounty_reason or "当前不可出手")
        embed = build_faction_embed(
            snapshot,
            target_count=len(targets),
            robbery_status_text=robbery_status_text,
            bounty_status_text=bounty_status_text,
        )
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return embed, FactionView(owner_user_id, snapshot=snapshot, targets=targets), broadcasts


async def join_faction_message(bot: XianBot, owner_user_id: int, display_name: str, faction_key: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        success, message = bot.faction_service.join_faction(character, faction_key)
        snapshot = await _sync_snapshot(bot, session, character)
        characters = await bot.character_service.list_characters(session)
        if character.faction == "righteous":
            targets = bot.faction_service.list_bounty_targets(characters)
        elif character.faction == "demonic":
            targets = bot.faction_service.list_robbery_targets(characters, character)
        else:
            targets = []
        lines = [f"当前阵营：**{snapshot.faction_name}**"]
        if snapshot.faction_title:
            lines.append(f"阵营称号：**{snapshot.faction_title}**")
        embed = build_faction_action_embed(snapshot, "阵营更定", message, lines, success=success)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return embed, FactionView(owner_user_id, snapshot=snapshot, targets=targets), broadcasts


async def build_bounty_hunt_message(bot: XianBot, owner_user_id: int, display_name: str, target_character_id: int):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        actor = creation.character
        characters = await bot.character_service.list_characters(session)
        target = next((entry for entry in characters if entry.id == target_character_id), None)
        result = bot.faction_service.challenge_bounty(actor, target) if target is not None else None
        snapshot = await _sync_snapshot(bot, session, actor)
        refreshed = await bot.character_service.list_characters(session)
        targets = bot.faction_service.list_bounty_targets(refreshed) if actor.faction == "righteous" else []
        if result is None:
            embed = build_faction_action_embed(snapshot, "悬赏讨伐", "未能找到该目标。", [], success=False)
        else:
            lines = []
            if result.target_name:
                lines.append(f"目标：**{result.target_name}**")
            if result.soul_delta:
                lines.append(f"器魂：`+{result.soul_delta}`")
            if result.luck_delta:
                lines.append(f"气运：`+{result.luck_delta}`")
            if result.virtue_delta:
                lines.append(f"善名：`+{result.virtue_delta}`")
            embed = build_faction_action_embed(snapshot, "悬赏讨伐", result.message, lines, success=result.success)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return embed, FactionView(owner_user_id, snapshot=snapshot, targets=targets), broadcasts


async def build_robbery_message(bot: XianBot, owner_user_id: int, display_name: str, target_character_id: int):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        actor = creation.character
        characters = await bot.character_service.list_characters(session)
        target = next((entry for entry in characters if entry.id == target_character_id), None)
        result = bot.faction_service.rob(actor, target) if target is not None else None
        snapshot = await _sync_snapshot(bot, session, actor)
        refreshed = await bot.character_service.list_characters(session)
        targets = bot.faction_service.list_robbery_targets(refreshed, actor) if actor.faction == "demonic" else []
        if result is None:
            embed = build_faction_action_embed(snapshot, "劫掠", "未能找到该目标。", [], success=False)
        else:
            lines = []
            if result.target_name:
                lines.append(f"目标：**{result.target_name}**")
            if result.soul_delta:
                lines.append(f"器魂：`+{result.soul_delta}`")
            if result.luck_delta:
                lines.append(f"气运：`+{result.luck_delta}`")
            if result.infamy_delta:
                lines.append(f"恶名：`+{result.infamy_delta}`")
            if result.same_faction_halved:
                lines.append("同为魔道，此次收益已减半。")
            embed = build_faction_action_embed(snapshot, "劫掠", result.message, lines, success=result.success)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return embed, FactionView(owner_user_id, snapshot=snapshot, targets=targets), broadcasts


async def run_private_ladder_sequence(
    bot: XianBot,
    interaction: discord.Interaction,
    *,
    owner_user_id: int,
    display_name: str,
    target_rank: int,
) -> None:
    embed, view, broadcasts = await build_challenge_message(bot, owner_user_id, display_name, target_rank)
    result = view.result
    challenger = view.challenger_snapshot
    defender = view.defender_snapshot

    preview_embed = build_ladder_round_embed(challenger, defender, result, preview=True)
    await interaction.response.send_message(embed=preview_embed, ephemeral=True)
    battle = result.battle
    if battle is None:
        await interaction.edit_original_response(embed=embed, view=view)
        await _send_broadcasts(bot, broadcasts)
        return

    for round_no in range(1, battle.rounds + 1):
        await asyncio.sleep(1.3)
        final = round_no == battle.rounds
        round_embed = build_ladder_round_embed(challenger, defender, result, preview=False, round_no=round_no, final=final)
        await interaction.edit_original_response(embed=round_embed, view=view if final else None)

    await _send_broadcasts(bot, broadcasts)


async def build_challenge_message(bot: XianBot, owner_user_id: int, display_name: str, target_rank: int):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        challenger = creation.character
        result = await bot.ladder_service.challenge(session, challenger, target_rank)
        defender = await bot.character_service.get_character_by_rank(session, result.defender_rank_after or target_rank)
        challenger_snapshot = await _sync_snapshot(bot, session, challenger)
        defender_snapshot = await _sync_snapshot(bot, session, defender) if defender is not None else challenger_snapshot
        targets = await bot.ladder_service.get_challenge_targets(session, challenger)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.reached_top_rank:
            broadcasts.append(f"【论道绝巅】{challenger_snapshot.player_name} 已登临论道榜首。")
        await session.commit()
    return (
        build_ladder_battle_embed(challenger_snapshot, defender_snapshot, result),
        LeaderboardView(owner_user_id, "ladder", targets, result=result, challenger_snapshot=challenger_snapshot, defender_snapshot=defender_snapshot),
        broadcasts,
    )


class OwnerLockedView(discord.ui.View):
    def __init__(self, owner_user_id: int, *, timeout: float | None = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_user_id = owner_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message("这张面板并非为你而开。", ephemeral=True)
        return False

    async def _apply(self, interaction: discord.Interaction, builder: Callable[[], Awaitable[tuple[discord.Embed, discord.ui.View | None, list[str]]]]) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await builder()
        await interaction.response.edit_message(embed=embed, view=view)
        for content in broadcasts:
            await bot.broadcast_service.broadcast(bot, content)


class PanelView(OwnerLockedView):
    def __init__(self, owner_user_id: int) -> None:
        super().__init__(owner_user_id)

    @discord.ui.button(label="刷新", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(
            interaction,
            lambda: build_panel_message(
                bot,
                interaction.user.id,
                interaction.user.display_name,
                avatar_url=interaction.user.display_avatar.url,
            ),
        )

    @discord.ui.button(label="登塔", style=discord.ButtonStyle.primary, row=0)
    async def tower_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await run_private_tower_sequence(bot, interaction, owner_user_id=interaction.user.id, display_name=interaction.user.display_name)

    @discord.ui.button(label="突破", style=discord.ButtonStyle.success, row=0)
    async def breakthrough_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, _, broadcasts = await build_breakthrough_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="修炼", style=discord.ButtonStyle.secondary, row=0)
    async def retreat_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_retreat_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="法宝", style=discord.ButtonStyle.secondary, row=0)
    async def reinforce_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_artifact_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="\u6e38\u5386", style=discord.ButtonStyle.secondary, row=1)
    async def travel_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_travel_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="论道", style=discord.ButtonStyle.secondary, row=1)
    async def ranking_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_leaderboard_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="阵营", style=discord.ButtonStyle.secondary, row=1)
    async def faction_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_faction_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="改命", style=discord.ButtonStyle.primary, row=1)
    async def rewrite_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_fate_rewrite_confirm_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)

    @discord.ui.button(label="轮回", style=discord.ButtonStyle.danger, row=1)
    async def reincarnate_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await build_reincarnation_confirm_message(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)


class ReincarnationConfirmView(OwnerLockedView):
    def __init__(self, owner_user_id: int) -> None:
        super().__init__(owner_user_id)
        self._add_confirm_button()
        self._add_cancel_button()

    def _add_confirm_button(self) -> None:
        button = discord.ui.Button(label="确认轮回", row=0, style=discord.ButtonStyle.danger)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, _, broadcasts = await build_reincarnation_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=None)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_cancel_button(self) -> None:
        button = discord.ui.Button(label="取消", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(content="已取消本次轮回。", embed=None, view=None)

        button.callback = callback
        self.add_item(button)


class FateRewriteConfirmView(OwnerLockedView):
    def __init__(self, owner_user_id: int, *, can_confirm: bool) -> None:
        super().__init__(owner_user_id)
        self._add_confirm_button(disabled=not can_confirm)
        self._add_cancel_button()

    def _add_confirm_button(self, *, disabled: bool) -> None:
        button = discord.ui.Button(label="确认改命", row=0, style=discord.ButtonStyle.primary, disabled=disabled)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, _, broadcasts = await build_fate_rewrite_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=None)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_cancel_button(self) -> None:
        button = discord.ui.Button(label="取消", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(content="已取消本次改命。", embed=None, view=None)

        button.callback = callback
        self.add_item(button)


class TowerRunView(OwnerLockedView):
    def __init__(self, owner_user_id: int, *, can_retry: bool) -> None:
        super().__init__(owner_user_id)
        self.can_retry = can_retry
        self._add_retry_button()
        self._add_exit_button()

    def _add_retry_button(self) -> None:
        button = discord.ui.Button(
            label="再次挑战",
            row=0,
            style=discord.ButtonStyle.primary,
            disabled=not self.can_retry,
        )

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await run_private_tower_sequence(
                bot,
                interaction,
                owner_user_id=interaction.user.id,
                display_name=interaction.user.display_name,
                edit_existing=True,
            )

        button.callback = callback
        self.add_item(button)

    def _add_exit_button(self) -> None:
        button = discord.ui.Button(label="退出", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(content="已退出本次通天塔界面。", embed=None, view=None)

        button.callback = callback
        self.add_item(button)


class ReinforceRenameModal(discord.ui.Modal, title="为本命法宝赐名"):
    artifact_name = discord.ui.TextInput(label="新名字", placeholder="请输入 2~12 个字符", max_length=12)

    def __init__(self, owner_user_id: int) -> None:
        super().__init__()
        self.owner_user_id = owner_user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view, broadcasts = await rename_artifact_message(bot, interaction.user.id, interaction.user.display_name, str(self.artifact_name))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await _send_broadcasts(bot, broadcasts)


class ArtifactOverviewView(OwnerLockedView):
    def __init__(self, owner_user_id: int) -> None:
        super().__init__(owner_user_id)
        self._add_open_reinforce_button()
        self._add_open_refine_button()
        self._add_rename_button()

    def _add_open_reinforce_button(self) -> None:
        button = discord.ui.Button(label="强化", row=0, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_reinforce_panel_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_open_refine_button(self) -> None:
        button = discord.ui.Button(label="洗炼", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_refine_panel_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_rename_button(self) -> None:
        button = discord.ui.Button(label="改名", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(ReinforceRenameModal(self.owner_user_id))

        button.callback = callback
        self.add_item(button)


class ArtifactReinforceView(OwnerLockedView):
    def __init__(self, owner_user_id: int) -> None:
        super().__init__(owner_user_id)
        self._add_reinforce_button()

    def _add_reinforce_button(self) -> None:
        button = discord.ui.Button(label="执行强化", row=0, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_reinforce_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)


class ArtifactRefineView(OwnerLockedView):
    def __init__(self, owner_user_id: int, panel_state) -> None:
        super().__init__(owner_user_id)
        self._add_save_button(disabled=not panel_state.has_pending)
        pending_slots = {slot.slot for slot in panel_state.pending_slots if slot.affix_id}
        for slot_view in panel_state.current_slots:
            self._add_refine_button(
                slot=slot_view.slot,
                unlock_level=slot_view.unlock_level,
                unlocked=slot_view.unlocked,
                highlighted=slot_view.slot in pending_slots,
            )

    def _add_save_button(self, *, disabled: bool) -> None:
        button = discord.ui.Button(label="保存待选", row=0, style=discord.ButtonStyle.success, disabled=disabled)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_save_affixes_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_refine_button(self, *, slot: int, unlock_level: int, unlocked: bool, highlighted: bool) -> None:
        label = f"槽{slot}" if unlocked else f"槽{slot}（+{unlock_level}解锁）"
        style = discord.ButtonStyle.primary if highlighted and unlocked else discord.ButtonStyle.secondary
        button = discord.ui.Button(label=label, row=1, style=style, disabled=not unlocked)

        async def callback(interaction: discord.Interaction, slot_no: int = slot) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_refine_affix_message(bot, interaction.user.id, interaction.user.display_name, slot_no)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)


class RetreatView(OwnerLockedView):
    def __init__(self, owner_user_id: int, *, is_retreating: bool) -> None:
        super().__init__(owner_user_id)
        self._add_start_button(disabled=is_retreating)
        self._add_stop_button(disabled=not is_retreating)

    def _add_start_button(self, *, disabled: bool) -> None:
        button = discord.ui.Button(label="开始闭关", row=0, style=discord.ButtonStyle.primary, disabled=disabled)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await start_retreat_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_stop_button(self, *, disabled: bool) -> None:
        button = discord.ui.Button(label="出关结算", row=0, style=discord.ButtonStyle.success, disabled=disabled)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await stop_retreat_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)


class TravelView(OwnerLockedView):
    def __init__(self, owner_user_id: int, *, snapshot) -> None:
        super().__init__(owner_user_id)
        if snapshot.is_traveling:
            self._add_refresh_button(row=0)
            self._add_stop_button(row=0, disabled=False)
        else:
            self._add_cycle_button(snapshot.travel_selected_duration_minutes, row=0)
            self._add_start_button(row=0)

    def _add_cycle_button(self, current_minutes: int, *, row: int) -> None:
        button = discord.ui.Button(label=f"切换时长（当前 {current_minutes}分）", row=row, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await cycle_travel_duration_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_start_button(self, *, row: int) -> None:
        button = discord.ui.Button(label="开始游历", row=row, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await start_travel_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_refresh_button(self, *, row: int) -> None:
        button = discord.ui.Button(label="刷新", row=row, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_travel_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_stop_button(self, *, row: int, disabled: bool) -> None:
        button = discord.ui.Button(label="归来结算", row=row, style=discord.ButtonStyle.success, disabled=disabled)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await stop_travel_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)


class FactionTargetSelect(discord.ui.Select):
    def __init__(self, owner_user_id: int, *, mode: str, targets: list[FactionTarget]) -> None:
        self.owner_user_id = owner_user_id
        self.mode = mode
        options = []
        for target in targets[:25]:
            if mode == "bounty":
                description = f"{target.realm_display} · {target.faction_name} · 悬赏 {target.bounty_soul}"
            else:
                description = f"{target.realm_display} · {target.faction_name} · 气运 {target.luck} · 器魂 {target.soul}"
            options.append(discord.SelectOption(label=target.display_name[:100], description=description[:100], value=str(target.character_id)))
        placeholder = "选择要讨伐的悬赏目标" if mode == "bounty" else "选择要劫掠的目标"
        super().__init__(placeholder=placeholder, options=options, row=1, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("这张面板并非为你而开。", ephemeral=True)
            return
        bot: XianBot = interaction.client  # type: ignore[assignment]
        target_character_id = int(self.values[0])
        if self.mode == "bounty":
            embed, view, broadcasts = await build_bounty_hunt_message(bot, interaction.user.id, interaction.user.display_name, target_character_id)
        else:
            embed, view, broadcasts = await build_robbery_message(bot, interaction.user.id, interaction.user.display_name, target_character_id)
        await interaction.response.edit_message(embed=embed, view=view)
        await _send_broadcasts(bot, broadcasts)


class FactionView(OwnerLockedView):
    def __init__(self, owner_user_id: int, *, snapshot, targets: list[FactionTarget]) -> None:
        super().__init__(owner_user_id)
        self.snapshot = snapshot
        self.targets = targets
        self._add_refresh_button()
        if snapshot.faction_key == "neutral":
            self._add_join_button("righteous", "加入正道", discord.ButtonStyle.success, row=0)
            self._add_join_button("demonic", "堕入魔道", discord.ButtonStyle.danger, row=0)
            return
        if snapshot.faction_key == "righteous":
            self._add_board_button("bounty", "悬赏榜", row=0)
            self._add_board_button("righteous", "正道榜", row=0)
            self._add_board_button("demonic", "魔道榜", row=0)
            if targets:
                self.add_item(FactionTargetSelect(owner_user_id, mode="bounty", targets=targets))
            return
        self._add_board_button("righteous", "正道榜", row=0)
        self._add_board_button("demonic", "魔道榜", row=0)
        self._add_board_button("bounty", "悬赏榜", row=0)
        if targets:
            self.add_item(FactionTargetSelect(owner_user_id, mode="robbery", targets=targets))

    def _add_refresh_button(self) -> None:
        button = discord.ui.Button(label="刷新", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_faction_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_join_button(self, faction_key: str, label: str, style: discord.ButtonStyle, *, row: int) -> None:
        button = discord.ui.Button(label=label, row=row, style=style)

        async def callback(interaction: discord.Interaction, target_faction: str = faction_key) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await join_faction_message(bot, interaction.user.id, interaction.user.display_name, target_faction)
            await interaction.response.edit_message(embed=embed, view=view)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)

    def _add_board_button(self, category: str, label: str, *, row: int) -> None:
        button = discord.ui.Button(label=label, row=row, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction, category_name: str = category) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view, broadcasts = await build_leaderboard_message(bot, interaction.user.id, interaction.user.display_name, category=category_name)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await _send_broadcasts(bot, broadcasts)

        button.callback = callback
        self.add_item(button)


class LeaderboardView(OwnerLockedView):
    def __init__(
        self,
        owner_user_id: int,
        category: str,
        challenge_targets: list,
        *,
        result=None,
        challenger_snapshot=None,
        defender_snapshot=None,
    ) -> None:
        super().__init__(owner_user_id)
        self.result = result
        self.challenger_snapshot = challenger_snapshot
        self.defender_snapshot = defender_snapshot
        self._add_category_button("ladder", "论道", category == "ladder", row=0)
        self._add_category_button("power", "战力", category == "power", row=0)
        self._add_category_button("realm_power", "同境", category == "realm_power", row=0)
        self._add_category_button("tower", "通天塔", category == "tower", row=0)
        self._add_category_button("artifact", "法宝", category == "artifact", row=1)
        self._add_category_button("realm", "境界", category == "realm", row=1)
        self._add_category_button("righteous", "正道", category == "righteous", row=1)
        self._add_category_button("demonic", "魔道", category == "demonic", row=1)
        self._add_category_button("bounty", "悬赏", category == "bounty", row=2)
        if category == "ladder":
            for target in challenge_targets[:5]:
                self._add_challenge_button(target.rank, target.display_name, row=3)

    def _add_category_button(self, category: str, label: str, active: bool, *, row: int) -> None:
        style = discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
        button = discord.ui.Button(label=label, row=row, style=style)

        async def callback(interaction: discord.Interaction, category_name: str = category) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await self._apply(
                interaction,
                lambda: build_leaderboard_message(bot, interaction.user.id, interaction.user.display_name, category=category_name),
            )

        button.callback = callback
        self.add_item(button)

    def _add_challenge_button(self, rank: int, display_name: str, *, row: int) -> None:
        button = discord.ui.Button(label=f"挑战#{rank} {display_name[:4]}", row=row, style=discord.ButtonStyle.danger)

        async def callback(interaction: discord.Interaction, target_rank: int = rank) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await run_private_ladder_sequence(
                bot,
                interaction,
                owner_user_id=interaction.user.id,
                display_name=interaction.user.display_name,
                target_rank=target_rank,
            )

        button.callback = callback
        self.add_item(button)
