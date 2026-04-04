from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from bot.ui.panel import (
    build_breakthrough_embed,
    build_ladder_battle_embed,
    build_panel_embed,
    build_reincarnation_embed,
    build_reinforce_embed,
    build_tower_embed,
)
from bot.ui.ranking import build_leaderboard_embed

if TYPE_CHECKING:
    from bot.main import XianBot


def _info_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.orange())


async def _sync_snapshot(bot: XianBot, session, character, *, settle_idle: bool) -> tuple:
    if settle_idle:
        bot.idle_service.settle(character)
    bot.ladder_service.reset_daily_attempts_if_needed(character)
    bot.character_service.refresh_combat_power(character)
    title, honor_tags = await bot.ranking_service.get_titles(session, character)
    character.title = title
    snapshot = bot.character_service.build_snapshot(
        character,
        title=title,
        honor_tags=honor_tags,
        idle_minutes=bot.idle_service.current_idle_minutes(character),
    )
    return snapshot


async def _load_active_character(bot: XianBot, user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, user_id, display_name)
        character = creation.character
        snapshot = await _sync_snapshot(bot, session, character, settle_idle=True)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return character.id, snapshot, broadcasts


async def build_panel_message(
    bot: XianBot,
    owner_user_id: int,
    display_name: str,
    *,
    target_user_id: int | None = None,
) -> tuple[discord.Embed, discord.ui.View | None, list[str]]:
    if target_user_id is not None and target_user_id != owner_user_id:
        async with bot.session_factory() as session:
            character = await bot.character_service.get_character_by_discord_id(session, target_user_id)
            if character is None:
                return _info_embed("未见道友", "此人尚未踏入仙途。"), None, []
            snapshot = await _sync_snapshot(bot, session, character, settle_idle=False)
            await session.commit()
        return build_panel_embed(snapshot), None, []

    _, snapshot, broadcasts = await _load_active_character(bot, owner_user_id, display_name)
    return build_panel_embed(snapshot), PanelView(owner_user_id), broadcasts


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
        viewer_snapshot = await _sync_snapshot(bot, session, viewer, settle_idle=True)
        leaderboard = await bot.ranking_service.build_leaderboard(session, category, viewer)
        targets = await bot.ladder_service.get_challenge_targets(session, viewer) if category == "ladder" else []
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_leaderboard_embed(leaderboard, viewer_snapshot), LeaderboardView(owner_user_id, category, targets), broadcasts


async def build_tower_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _sync_snapshot(bot, session, character, settle_idle=True)
        result = bot.tower_service.run_tower(character)
        snapshot = await _sync_snapshot(bot, session, character, settle_idle=False)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.highest_floor_after > result.highest_floor_before and result.highest_floor_after % 25 == 0:
            broadcasts.append(f"【塔影留名】{snapshot.player_name} 已踏破通天塔第 {result.highest_floor_after} 层。")
        await session.commit()
    return build_tower_embed(snapshot, result), PanelView(owner_user_id), broadcasts


async def build_breakthrough_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _sync_snapshot(bot, session, character, settle_idle=True)
        result = bot.breakthrough_service.attempt_breakthrough(character)
        snapshot = await _sync_snapshot(bot, session, character, settle_idle=False)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.success and result.reached_new_realm:
            broadcasts.append(f"【大境将成】{snapshot.player_name} 已踏入 {snapshot.realm_display}。")
        await session.commit()
    return build_breakthrough_embed(snapshot, result), PanelView(owner_user_id), broadcasts


async def build_reinforce_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _sync_snapshot(bot, session, character, settle_idle=True)
        result = bot.artifact_service.reinforce(character.artifact, bot.character_service.get_stage(character))
        bot.character_service.refresh_combat_power(character)
        snapshot = await _sync_snapshot(bot, session, character, settle_idle=False)
        await session.commit()
    broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
    return build_reinforce_embed(snapshot, result), PanelView(owner_user_id), broadcasts


async def build_reincarnation_message(bot: XianBot, owner_user_id: int, display_name: str):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        character = creation.character
        await _sync_snapshot(bot, session, character, settle_idle=True)
        result = await bot.character_service.reincarnate(session, character)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.success:
            await bot.ladder_service.move_to_bottom(session, character)
        snapshot = await _sync_snapshot(bot, session, character, settle_idle=False)
        if result.broadcast_text:
            broadcasts.append(result.broadcast_text)
        await session.commit()
    return build_reincarnation_embed(snapshot, result.message), PanelView(owner_user_id), broadcasts


async def build_challenge_message(bot: XianBot, owner_user_id: int, display_name: str, target_rank: int):
    async with bot.session_factory() as session:
        creation = await bot.character_service.get_or_create_character(session, owner_user_id, display_name)
        challenger = creation.character
        await _sync_snapshot(bot, session, challenger, settle_idle=True)
        result = await bot.ladder_service.challenge(session, challenger, target_rank)
        defender = await bot.character_service.get_character_by_rank(session, result.defender_rank_after or target_rank)
        challenger_snapshot = await _sync_snapshot(bot, session, challenger, settle_idle=False)
        defender_snapshot = await _sync_snapshot(bot, session, defender, settle_idle=False) if defender is not None else challenger_snapshot
        targets = await bot.ladder_service.get_challenge_targets(session, challenger)
        broadcasts = [creation.broadcast_text] if creation.broadcast_text else []
        if result.reached_top_rank:
            broadcasts.append(f"【论道绝巅】{challenger_snapshot.player_name} 已登临论道榜首。")
        await session.commit()
    return build_ladder_battle_embed(challenger_snapshot, defender_snapshot, result), LeaderboardView(owner_user_id, "ladder", targets), broadcasts


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

    @discord.ui.button(label="登塔", style=discord.ButtonStyle.primary)
    async def tower_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(interaction, lambda: build_tower_message(bot, interaction.user.id, interaction.user.display_name))

    @discord.ui.button(label="突破", style=discord.ButtonStyle.success)
    async def breakthrough_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(interaction, lambda: build_breakthrough_message(bot, interaction.user.id, interaction.user.display_name))

    @discord.ui.button(label="锻宝", style=discord.ButtonStyle.secondary)
    async def reinforce_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(interaction, lambda: build_reinforce_message(bot, interaction.user.id, interaction.user.display_name))

    @discord.ui.button(label="榜单", style=discord.ButtonStyle.secondary)
    async def ranking_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(interaction, lambda: build_leaderboard_message(bot, interaction.user.id, interaction.user.display_name))

    @discord.ui.button(label="轮回", style=discord.ButtonStyle.danger)
    async def reincarnate_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        await self._apply(interaction, lambda: build_reincarnation_message(bot, interaction.user.id, interaction.user.display_name))


class LeaderboardView(OwnerLockedView):
    def __init__(self, owner_user_id: int, category: str, challenge_targets: list) -> None:
        super().__init__(owner_user_id)
        self._add_back_button()
        self._add_category_button("ladder", "论道", category == "ladder")
        self._add_category_button("power", "战力", category == "power")
        self._add_category_button("tower", "通天塔", category == "tower")
        self._add_category_button("artifact", "法宝", category == "artifact")
        if category == "ladder":
            for target in challenge_targets[:5]:
                self._add_challenge_button(target.rank, target.display_name)

    def _add_back_button(self) -> None:
        button = discord.ui.Button(label="返回", row=0, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await self._apply(interaction, lambda: build_panel_message(bot, interaction.user.id, interaction.user.display_name))

        button.callback = callback
        self.add_item(button)

    def _add_category_button(self, category: str, label: str, active: bool) -> None:
        style = discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
        button = discord.ui.Button(label=label, row=0, style=style)

        async def callback(interaction: discord.Interaction, category_name: str = category) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await self._apply(
                interaction,
                lambda: build_leaderboard_message(bot, interaction.user.id, interaction.user.display_name, category=category_name),
            )

        button.callback = callback
        self.add_item(button)

    def _add_challenge_button(self, rank: int, display_name: str) -> None:
        button = discord.ui.Button(label=f"挑战#{rank} {display_name[:4]}", row=1, style=discord.ButtonStyle.danger)

        async def callback(interaction: discord.Interaction, target_rank: int = rank) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            await self._apply(
                interaction,
                lambda: build_challenge_message(bot, interaction.user.id, interaction.user.display_name, target_rank),
            )

        button.callback = callback
        self.add_item(button)
