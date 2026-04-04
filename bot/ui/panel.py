from __future__ import annotations

import discord

from bot.services.artifact_service import ReinforceResult
from bot.services.breakthrough_service import BreakthroughResult
from bot.services.character_service import CharacterSnapshot
from bot.services.ladder_service import LadderChallengeResult
from bot.services.tower_service import TowerFloorResult, TowerRunResult
from bot.utils.formatters import RARITY_BADGES, RARITY_COLORS, format_big_number, format_duration_minutes, format_progress, format_qi


def build_panel_embed(
    snapshot: CharacterSnapshot,
    *,
    avatar_url: str | None = None,
    idle_notice: str | None = None,
) -> discord.Embed:
    progress = format_progress(snapshot.cultivation, snapshot.cultivation_max, width=8)
    percent = int((snapshot.cultivation / snapshot.cultivation_max) * 100) if snapshot.cultivation_max else 0
    honor_line = " · ".join(f"`{tag}`" for tag in snapshot.honor_tags) if snapshot.honor_tags else "暂无额外荣誉"

    embed = discord.Embed(
        title=f"【{snapshot.realm_display}】{snapshot.player_name}",
        description=(
            f"👑 **{snapshot.title}**\n"
            f"⚡ 总战力：**{format_big_number(snapshot.combat_power)}**\n"
            f"🔮 命格：`{RARITY_BADGES[snapshot.fate_rarity]}` **{snapshot.fate_name}** · {snapshot.fate_summary}\n"
            f"🏷 荣誉：{honor_line}"
        ),
        color=RARITY_COLORS[snapshot.fate_rarity],
    )
    embed.add_field(
        name="📍 牌面",
        value=(
            f"当前论道：`#{snapshot.current_ladder_rank}`\n"
            f"历史最高：`#{snapshot.best_ladder_rank}`\n"
            f"最高塔层：`{snapshot.historical_highest_floor}` 层\n"
            f"轮回次数：`{snapshot.reincarnation_count}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🗡 三维",
        value=(
            f"⚔️ 杀伐：`{format_big_number(snapshot.total_atk)}`\n"
            f"🛡 护体：`{format_big_number(snapshot.total_def)}`\n"
            f"💨 身法：`{format_big_number(snapshot.total_agi)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🧰 本命",
        value=(
            f"法宝：**{snapshot.artifact_name}** `+{snapshot.artifact_level}`\n"
            f"器魂：`{snapshot.soul_shards}`\n"
            f"闭关：第 `{max(snapshot.highest_floor, 1)}` 层 · {format_duration_minutes(snapshot.idle_minutes)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="📈 修为进境",
        value=(
            f"修为：`{format_big_number(snapshot.cultivation)} / {format_big_number(snapshot.cultivation_max)}`\n"
            f"{progress} `{percent}%`\n"
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`"
        ),
        inline=False,
    )
    if idle_notice:
        embed.add_field(name="💤 挂机补算", value=idle_notice, inline=False)
    embed.add_field(name="✨ 近况", value=snapshot.last_highlight_text, inline=False)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    return embed


def build_tower_floor_embed(
    snapshot: CharacterSnapshot,
    floor_result: TowerFloorResult,
    *,
    preview: bool,
    run_result: TowerRunResult | None = None,
    idle_notice: str | None = None,
) -> discord.Embed:
    title_suffix = " · 守关" if floor_result.is_boss else ""
    color = discord.Color.blurple() if preview else (discord.Color.green() if floor_result.victory else discord.Color.orange())
    embed = discord.Embed(
        title=f"通天塔 · 第 {floor_result.floor} 层{title_suffix}",
        description=f"{snapshot.player_name} 凝神而立，直面 {floor_result.enemy_name}。",
        color=color,
    )

    player_max_hp = floor_result.battle.challenger_max_hp
    enemy_max_hp = floor_result.battle.defender_max_hp
    if preview:
        enemy_status = f"**{floor_result.enemy_name}**\n♥ 血量：`{format_big_number(enemy_max_hp)} / {format_big_number(enemy_max_hp)}`"
        player_status = f"**{snapshot.player_name}**\n♥ 血量：`{format_big_number(player_max_hp)} / {format_big_number(player_max_hp)}`"
        report_text = "未开战，气机流转，杀机未发。"
        reward_text = "胜出后方可结算。"
    else:
        enemy_after = floor_result.battle.defender_hp_after
        player_after = floor_result.battle.challenger_hp_after
        enemy_status = (
            f"**{floor_result.enemy_name}**\n💀 已倒下"
            if enemy_after <= 0
            else f"**{floor_result.enemy_name}**\n♥ 血量：`{format_big_number(enemy_after)} / {format_big_number(enemy_max_hp)}`"
        )
        player_status = (
            f"**{snapshot.player_name}**\n💀 已败退"
            if player_after <= 0
            else f"**{snapshot.player_name}**\n♥ 血量：`{format_big_number(player_after)} / {format_big_number(player_max_hp)}`"
        )
        report_text = _battle_excerpt(floor_result.battle, limit=8, mode="tower")
        reward_text = _tower_reward_text(floor_result)

    embed.add_field(name="🗿 守塔者", value=enemy_status, inline=True)
    embed.add_field(name="🧍 你", value=player_status, inline=True)
    embed.add_field(name="📜 战报", value=report_text, inline=False)
    embed.add_field(name="🎁 奖励", value=reward_text, inline=False)
    if idle_notice and (preview or run_result is not None):
        embed.add_field(name="💤 挂机补算", value=idle_notice, inline=False)

    if run_result is not None:
        embed.add_field(
            name="✨ 本轮小结",
            value=(
                f"气机：`{run_result.qi_before} -> {run_result.qi_after}`\n"
                f"新高：`{run_result.highest_floor_before} -> {run_result.highest_floor_after}`\n"
                f"总计：器魂 `+{run_result.total_soul}` · 修为 `+{format_big_number(run_result.total_cultivation)}`"
            ),
            inline=False,
        )
        embed.set_footer(text=run_result.message)
    return embed


def build_tower_embed(snapshot: CharacterSnapshot, result: TowerRunResult, *, idle_notice: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 通天塔战报",
        description=(
            f"{result.message}\n"
            f"气机：`{result.qi_before} -> {result.qi_after}`\n"
            f"新高：`{result.highest_floor_before} -> {result.highest_floor_after}`\n"
            f"本轮获得器魂：`{result.total_soul}` · 修为：`{format_big_number(result.total_cultivation)}`"
        ),
        color=discord.Color.blurple(),
    )
    if result.floors:
        lines = []
        for floor_result in result.floors:
            suffix = "守关" if floor_result.is_boss else "层战"
            status = "胜" if floor_result.victory else "败"
            reward_text = _tower_reward_text(floor_result)
            lines.append(f"第 {floor_result.floor} 层 {suffix} {status} | {floor_result.enemy_name} | {reward_text}")
        embed.add_field(name="层数结算", value="\n".join(lines[:5]), inline=False)
        embed.add_field(name="战斗截取", value=_battle_excerpt(result.floors[-1].battle, limit=4, mode="tower"), inline=False)
    if idle_notice:
        embed.add_field(name="💤 挂机补算", value=idle_notice, inline=False)
    return embed


def build_breakthrough_embed(snapshot: CharacterSnapshot, result: BreakthroughResult, *, idle_notice: str | None = None) -> discord.Embed:
    color = discord.Color.green() if result.success else discord.Color.orange()
    embed = discord.Embed(title=f"{snapshot.player_name} · 突破", description=result.message, color=color)
    embed.add_field(
        name="当前状态",
        value=(
            f"境界：`{snapshot.realm_display}`\n"
            f"修为：`{format_big_number(snapshot.cultivation)} / {format_big_number(snapshot.cultivation_max)}`\n"
            f"最高塔层：`{snapshot.highest_floor}`"
        ),
        inline=False,
    )
    if idle_notice:
        embed.add_field(name="💤 挂机补算", value=idle_notice, inline=False)
    if result.required_floor is not None:
        embed.set_footer(text=f"当前突破门槛守关层：第 {result.required_floor} 层")
    return embed


def build_reinforce_embed(snapshot: CharacterSnapshot, result: ReinforceResult) -> discord.Embed:
    color = discord.Color.green() if result.success else discord.Color.orange()
    embed = discord.Embed(title=f"{snapshot.player_name} · 锻宝", description=result.message, color=color)
    rate = f"{int(result.success_rate * 100)}%" if result.success_rate else "0%"
    embed.add_field(
        name="本次锻宝",
        value=(
            f"法宝：**{snapshot.artifact_name}**\n"
            f"强化：`+{result.level_before} -> +{result.level_after}`\n"
            f"器魂消耗：`{result.soul_cost}`\n"
            f"成功率：`{rate}`"
        ),
        inline=False,
    )
    if result.success:
        embed.add_field(
            name="成长落点",
            value=f"杀伐 +`{result.gained_atk}` · 护体 +`{result.gained_def}` · 身法 +`{result.gained_agi}`",
            inline=False,
        )
    return embed


def build_reincarnation_embed(snapshot: CharacterSnapshot, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 轮回重修",
        description=message,
        color=RARITY_COLORS[snapshot.fate_rarity],
    )
    embed.add_field(
        name="新命数",
        value=f"`{RARITY_BADGES[snapshot.fate_rarity]}` **{snapshot.fate_name}** · {snapshot.fate_summary}",
        inline=False,
    )
    embed.add_field(
        name="重修后",
        value=(
            f"境界：`{snapshot.realm_display}`\n"
            f"塔层：`{snapshot.highest_floor}`\n"
            f"论道：`#{snapshot.current_ladder_rank}`\n"
            f"轮回次数：`{snapshot.reincarnation_count}`"
        ),
        inline=False,
    )
    return embed


def _ladder_hp_after_round(
    challenger: CharacterSnapshot,
    defender: CharacterSnapshot,
    battle,
    round_no: int,
) -> tuple[int, int]:
    challenger_hp = battle.challenger_max_hp
    defender_hp = battle.defender_max_hp
    for action in battle.logs:
        if action.round_no > round_no:
            break
        if action.target_name == challenger.player_name:
            challenger_hp = action.target_hp_after
        elif action.target_name == defender.player_name:
            defender_hp = action.target_hp_after
    return challenger_hp, defender_hp


def _ladder_round_report(battle, round_no: int) -> str:
    lines = []
    for action in battle.logs:
        if action.round_no != round_no:
            continue
        if action.dodged:
            lines.append(f"{action.actor_name} 一击落空，被 {action.target_name} 避开。")
            continue
        critical = "暴击" if action.critical else "命中"
        lines.append(f"{action.actor_name} {critical} {action.target_name}，造成 {format_big_number(action.damage)} 点伤害。")
    return "\n".join(lines) if lines else "这一回合杀机未成。"


def build_ladder_round_embed(
    challenger: CharacterSnapshot,
    defender: CharacterSnapshot,
    result: LadderChallengeResult,
    *,
    preview: bool,
    round_no: int | None = None,
    final: bool = False,
) -> discord.Embed:
    battle = result.battle
    color = discord.Color.blurple() if preview else (discord.Color.green() if result.battle and result.battle.challenger_won else discord.Color.orange())
    embed = discord.Embed(
        title=f"论道 · 挑战第 {result.defender_rank_before} 席",
        description=f"{challenger.player_name} 向 {defender.player_name} 发起论道。",
        color=color,
    )

    if preview or battle is None or round_no is None:
        challenger_hp = challenger.total_def * 10
        defender_hp = defender.total_def * 10
        report_text = "双方灵机相引，尚未真正出手。"
    else:
        challenger_hp, defender_hp = _ladder_hp_after_round(challenger, defender, battle, round_no)
        report_text = _ladder_round_report(battle, round_no)

    challenger_state = (
        f"**{challenger.player_name}** · {challenger.realm_display}\n"
        f"称号：**{challenger.title}**\n"
        + ("💀 已败退" if challenger_hp <= 0 else f"♥ 血量：`{format_big_number(challenger_hp)} / {format_big_number(battle.challenger_max_hp if battle else challenger.total_def * 10)}`")
    )
    defender_state = (
        f"**{defender.player_name}** · {defender.realm_display}\n"
        f"称号：**{defender.title}**\n"
        + ("💀 已落败" if defender_hp <= 0 else f"♥ 血量：`{format_big_number(defender_hp)} / {format_big_number(battle.defender_max_hp if battle else defender.total_def * 10)}`")
    )
    embed.add_field(name="🧍 你", value=challenger_state, inline=True)
    embed.add_field(name="🗿 对手", value=defender_state, inline=True)
    if preview:
        embed.add_field(name="📜 战报", value=report_text, inline=False)
    else:
        embed.add_field(name=f"📜 第 {round_no} 回合", value=report_text, inline=False)
    if final:
        embed.add_field(
            name="✨ 论果",
            value=(
                f"你：`#{result.challenger_rank_before} -> #{result.challenger_rank_after}`\n"
                f"对手：`#{result.defender_rank_before} -> #{result.defender_rank_after}`\n"
                f"剩余挑战：`{result.remaining_attempts}` 次\n"
                f"结果：{result.message}"
            ),
            inline=False,
        )
    return embed


def build_ladder_battle_embed(
    challenger: CharacterSnapshot,
    defender: CharacterSnapshot,
    result: LadderChallengeResult,
) -> discord.Embed:
    color = discord.Color.green() if result.battle and result.battle.challenger_won else discord.Color.orange()
    embed = discord.Embed(title=f"{challenger.player_name} · 论道", description=result.message, color=color)
    embed.add_field(
        name="双方牌面",
        value=(
            f"你：`#{result.challenger_rank_before} -> #{result.challenger_rank_after}` · {challenger.realm_display} · 战力 {format_big_number(challenger.combat_power)}\n"
            f"对手：`#{result.defender_rank_before} -> #{result.defender_rank_after}` · {defender.player_name} · {defender.realm_display} · 战力 {format_big_number(defender.combat_power)}"
        ),
        inline=False,
    )
    if result.battle is not None:
        embed.add_field(name="战报截取", value=_battle_excerpt(result.battle, limit=6), inline=False)
        embed.set_footer(text=f"今日剩余论道次数：{result.remaining_attempts}")
    return embed


def _tower_reward_text(floor_result: TowerFloorResult) -> str:
    reward = []
    if floor_result.reward_soul:
        reward.append(f"器魂+{floor_result.reward_soul}")
    if floor_result.reward_cultivation:
        reward.append(f"修为+{format_big_number(floor_result.reward_cultivation)}")
    if floor_result.bonus_drop_triggered:
        reward.append("额外掉落触发")
    return " · ".join(reward) if reward else "无额外收获"


def _battle_excerpt(battle, limit: int, *, mode: str = "ladder") -> str:
    lines = []
    for action in battle.logs[-limit:]:
        if action.dodged:
            lines.append(f"第 {action.round_no} 回合 | {action.actor_name} 一击落空，被 {action.target_name} 避开。")
            continue
        critical = "暴击" if action.critical else "命中"
        lines.append(f"第 {action.round_no} 回合 | {action.actor_name} {critical} {action.target_name}，造成 {format_big_number(action.damage)} 点伤害，余血 {format_big_number(action.target_hp_after)}。")
    if battle.reached_round_limit:
        lines.append("十合战罢，挑战方未能夺位。" if mode == "ladder" else "十合战罢，此层未能踏破。")
    return "\n".join(lines) if lines else "此战过于短促，未留战痕。"
