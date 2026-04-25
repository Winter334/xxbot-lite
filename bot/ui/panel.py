from __future__ import annotations

import discord

from bot.services.breakthrough_service import BreakthroughResult
from bot.services.character_service import CharacterSnapshot
from bot.services.combat_service import CombatService
from bot.services.idle_service import IdleSettlement
from bot.services.ladder_service import LadderChallengeResult
from bot.services.tower_service import TowerFloorResult, TowerRunResult
from bot.services.travel_service import TravelSettlement
from bot.utils.formatters import RARITY_BADGES, RARITY_COLORS, format_big_number, format_duration_minutes, format_progress, format_qi


MAX_BATTLE_ROUNDS = CombatService.max_rounds


def build_panel_embed(
    snapshot: CharacterSnapshot,
    *,
    avatar_url: str | None = None,
    idle_notice: str | None = None,
) -> discord.Embed:
    progress = format_progress(snapshot.cultivation, snapshot.cultivation_max, width=8)
    percent = int((snapshot.cultivation / snapshot.cultivation_max) * 100) if snapshot.cultivation_max else 0
    honor_line = " · ".join(f"`{tag}`" for tag in snapshot.honor_tags) if snapshot.honor_tags else "暂无额外荣誉"
    if snapshot.is_traveling:
        state_text = f"游历中 · {format_duration_minutes(snapshot.travel_minutes)}（最多计入 {format_duration_minutes(snapshot.travel_duration_minutes)}）"
    elif snapshot.is_retreating:
        state_prefix = "炼魂中" if snapshot.retreat_mode == "soul" else "闭关中"
        state_text = f"{state_prefix} · {format_duration_minutes(snapshot.idle_minutes)}"
    else:
        state_text = "未在闭关/游历"

    embed = discord.Embed(
        title=f"【{snapshot.realm_display}】{snapshot.player_name}",
        description=(
            f"👑 **{snapshot.title}**\n"
            f"⚔️ 总战力：**{format_big_number(snapshot.combat_power)}**\n"
            f"🔭 命格：`{RARITY_BADGES[snapshot.fate_rarity]}` **{snapshot.fate_name}** · {snapshot.fate_summary}\n"
            f"☯ 阵营：**{snapshot.faction_name}**"
            f"{f' · {snapshot.faction_title}' if snapshot.faction_title else ''}\n"
            f"🏯 宗门：**{snapshot.sect_name or '散修'}**{f' · {snapshot.sect_role}' if snapshot.sect_role else ''}\n"
            f"🎖 荣誉：{honor_line}"
        ),
        color=RARITY_COLORS[snapshot.fate_rarity],
    )
    embed.add_field(
        name="📜 牌面",
        value=(
            f"当前论道：`#{snapshot.current_ladder_rank}`\n"
            f"历史最高：`#{snapshot.best_ladder_rank}`\n"
            f"历史塔层：`{snapshot.historical_highest_floor}` 层\n"
            f"轮回次数：`{snapshot.reincarnation_count}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🛡 三维",
        value=(
            f"⚔️ 杀伐：`{format_big_number(snapshot.total_atk)}`\n"
            f"🛡 护体：`{format_big_number(snapshot.total_def)}`\n"
            f"💨 身法：`{format_big_number(snapshot.total_agi)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🗿 本命",
        value=(
            f"法宝：**{snapshot.artifact_name}** `+{snapshot.artifact_level}`\n"
            f"器魂：`{snapshot.soul_shards}`\n"
            f"器灵：`{snapshot.spirit_name}`{f' · {snapshot.spirit_tier_name}' if snapshot.spirit_tier_name else ''}\n"
            f"神通：`{snapshot.spirit_power_name or '未显神通'}`\n"
            f"当前行藏：`{state_text}`"
        ),
        inline=False,
    )
    faction_lines = [f"气运：`{snapshot.luck}` · 可改命 `×{snapshot.rewrite_chances}`"]
    if snapshot.faction_key == "righteous":
        faction_lines.append(f"善名：`{snapshot.virtue}`")
    elif snapshot.faction_key == "demonic":
        faction_lines.append(f"恶名：`{snapshot.infamy}`")
        faction_lines.append(f"悬赏：`{snapshot.bounty_soul}`")
    else:
        faction_lines.append("善名：`0` · 恶名：`0`")
    embed.add_field(name="☯ 阵营信息", value="\n".join(faction_lines), inline=True)
    embed.add_field(
        name="🧭 游历遗痕",
        value=(
            f"杀伐：`{_format_signed_pct(snapshot.travel_atk_pct)}`\n"
            f"护体：`{_format_signed_pct(snapshot.travel_def_pct)}`\n"
            f"身法：`{_format_signed_pct(snapshot.travel_agi_pct)}`"
        ),
        inline=True,
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
        embed.add_field(name="🪷 出关所得", value=idle_notice, inline=False)
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
        enemy_status = f"**{floor_result.enemy_name}**\n❤ 血量：`{format_big_number(enemy_max_hp)} / {format_big_number(enemy_max_hp)}`"
        player_status = f"**{snapshot.player_name}**\n❤ 血量：`{format_big_number(player_max_hp)} / {format_big_number(player_max_hp)}`"
        report_text = "未开战，气机流转，杀机未发。"
        reward_text = "胜出后方可结算。"
    else:
        enemy_after = floor_result.battle.defender_hp_after
        player_after = floor_result.battle.challenger_hp_after
        enemy_status = (
            f"**{floor_result.enemy_name}**\n💥 已倒下"
            if enemy_after <= 0
            else f"**{floor_result.enemy_name}**\n❤ 血量：`{format_big_number(enemy_after)} / {format_big_number(enemy_max_hp)}`"
        )
        player_status = (
            f"**{snapshot.player_name}**\n💥 已败退"
            if player_after <= 0
            else f"**{snapshot.player_name}**\n❤ 血量：`{format_big_number(player_after)} / {format_big_number(player_max_hp)}`"
        )
        report_text = _battle_excerpt(floor_result.battle, limit=8, mode="tower")
        reward_text = _tower_reward_text(floor_result)

    embed.add_field(name="👹 守塔者", value=enemy_status, inline=True)
    embed.add_field(name="🧑 你", value=player_status, inline=True)
    embed.add_field(name="📜 战报", value=report_text, inline=False)
    embed.add_field(name="🎁 奖励", value=reward_text, inline=False)
    if idle_notice and (preview or run_result is not None):
        embed.add_field(name="🪷 出关所得", value=idle_notice, inline=False)

    if run_result is not None:
        embed.add_field(
            name="✅ 本轮小结",
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
            f"本轮获得器魂：`{result.total_soul}` · 修为：`+{format_big_number(result.total_cultivation)}`"
        ),
        color=discord.Color.blurple(),
    )
    if result.floors:
        lines = []
        for floor_result in result.floors:
            suffix = "守关" if floor_result.is_boss else "层战"
            status = "胜" if floor_result.victory else "负"
            reward_text = _tower_reward_text(floor_result)
            lines.append(f"第 {floor_result.floor} 层 {suffix} {status} | {floor_result.enemy_name} | {reward_text}")
        embed.add_field(name="层数结算", value="\n".join(lines[:5]), inline=False)
        embed.add_field(name="战斗截取", value=_battle_excerpt(result.floors[-1].battle, limit=4, mode="tower"), inline=False)
    if idle_notice:
        embed.add_field(name="🪷 出关所得", value=idle_notice, inline=False)
    return embed


def build_breakthrough_embed(snapshot: CharacterSnapshot, result: BreakthroughResult, *, idle_notice: str | None = None) -> discord.Embed:
    color = discord.Color.green() if result.success else discord.Color.orange()
    embed = discord.Embed(title=f"{snapshot.player_name} · 突破", description=result.message, color=color)
    embed.add_field(
        name="当前状态",
        value=(
            f"境界：`{snapshot.realm_display}`\n"
            f"修为：`{format_big_number(snapshot.cultivation)} / {format_big_number(snapshot.cultivation_max)}`\n"
            f"当前塔层：`{snapshot.highest_floor}`"
        ),
        inline=False,
    )
    if idle_notice:
        embed.add_field(name="🪷 出关所得", value=idle_notice, inline=False)
    if result.required_floor is not None:
        embed.set_footer(text=f"当前突破门槛守关层：第 {result.required_floor} 层")
    return embed


def build_retreat_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    mode_name = "炼魂" if snapshot.retreat_mode == "soul" else "修炼"
    status = f"{mode_name}中" if snapshot.is_retreating else "静室空悬"
    if snapshot.is_retreating and snapshot.retreat_mode == "soul":
        description = "地火在炉腹深处缓缓吞吐，本命灵韵被一点点逼出，凝作可触可见的器魂星芒。"
    elif snapshot.is_retreating:
        description = "洞府石门紧闭，四壁灵纹缓缓明灭，周身灵气沿着经脉往复流转，心海渐归寂定。"
    else:
        description = "洞府深处灯影幽微，一侧蒲团承接灵息，一侧古炉温养本命，静待你择一法门入定。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 洞府",
        description=description,
        color=discord.Color.teal() if snapshot.is_retreating else discord.Color.blurple(),
    )
    embed.add_field(
        name="当前状态",
        value=(
            f"状态：`{status}`\n"
            f"累计时长：`{format_duration_minutes(snapshot.idle_minutes)}`\n"
            f"本命器魂：`{snapshot.soul_shards}`"
        ),
        inline=False,
    )
    if snapshot.is_retreating:
        flavor_value = (
            "炉火如豆，映得本命灵光轻颤不止。"
            if snapshot.retreat_mode == "soul"
            else "灵息沿经脉回环不绝，丹田中已有潮汐起伏。"
        )
    else:
        flavor_value = "蒲团未暖，古炉犹温，静室之中仍留着上一轮吐纳后的余香。"
    embed.add_field(name="洞府气象", value=flavor_value, inline=False)
    return embed


def build_retreat_settlement_embed(snapshot: CharacterSnapshot, settlement: IdleSettlement, message: str) -> discord.Embed:
    mode_name = "炼魂" if settlement.retreat_mode == "soul" else "闭关"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 出关",
        description=message,
        color=discord.Color.green(),
    )
    embed.add_field(
        name=f"本次{mode_name}",
        value=(
            f"时长：`{format_duration_minutes(settlement.settled_minutes)}`\n"
            f"修为：`+{format_big_number(settlement.gained_cultivation)}`\n"
            f"器魂：`+{settlement.gained_soul}`\n"
            f"气运：`+{settlement.gained_luck}`\n"
            f"气机恢复：`+{settlement.recovered_qi}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="当前状态",
        value=(
            f"境界：`{snapshot.realm_display}`\n"
            f"修为：`{format_big_number(snapshot.cultivation)} / {format_big_number(snapshot.cultivation_max)}`\n"
            f"气机：`{format_qi(snapshot.qi_current, snapshot.qi_max)} {snapshot.qi_current}/{snapshot.qi_max}`"
        ),
        inline=False,
    )
    return embed


def build_travel_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    status = "游历中" if snapshot.is_traveling else "未动身"
    cap_minutes = snapshot.travel_duration_minutes if snapshot.travel_duration_minutes else 120
    remaining_minutes = max(0, cap_minutes - snapshot.travel_minutes) if snapshot.is_traveling else cap_minutes
    settled_events = min(12, snapshot.travel_minutes // 10) if snapshot.is_traveling else 0
    description = (
        "你正在山海之间寻机撞缘。每满 10 分钟结算 1 次奇遇，最多计入 12 次。"
        if snapshot.is_traveling
        else "动身后便会持续游历，直到你主动归来结算。游历与闭关互斥，中途归来只结算完整的 10 分钟路程。"
    )
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 游历与奇遇",
        description=description,
        color=discord.Color.gold() if snapshot.is_traveling else discord.Color.blurple(),
    )
    embed.add_field(
        name="当前状态",
        value=(
            f"状态：`{status}`\n"
            f"已行时长：`{format_duration_minutes(snapshot.travel_minutes)}`\n"
            f"剩余有效时长：`{format_duration_minutes(remaining_minutes)}`\n"
            f"当前可结算：`{settled_events}` 次\n"
            f"最多计入：`12` 次"
        ),
        inline=False,
    )
    embed.add_field(
        name="游历说明",
        value=(
            "- 直接开始游历，无需先选时长\n"
            "- 每 10 分钟结算 1 次事件\n"
            "- 单次游历最多结算 12 次\n"
            "- 游历与闭关互斥"
        ),
        inline=False,
    )
    embed.add_field(
        name="游历遗痕",
        value=(
            f"杀伐：`{_format_signed_pct(snapshot.travel_atk_pct)}`\n"
            f"护体：`{_format_signed_pct(snapshot.travel_def_pct)}`\n"
            f"身法：`{_format_signed_pct(snapshot.travel_agi_pct)}`"
        ),
        inline=False,
    )
    return embed


def build_travel_settlement_embed(snapshot: CharacterSnapshot, settlement: TravelSettlement) -> discord.Embed:
    color = discord.Color.green() if settlement.success else discord.Color.orange()
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 游历结算",
        description=settlement.message,
        color=color,
    )
    embed.add_field(
        name="本次结果",
        value=(
            f"结算次数：`{settlement.settled_events}`\n"
            f"有效时长：`{format_duration_minutes(settlement.settled_minutes)}`\n"
            f"器魂：`{'+' if settlement.total_soul > 0 else ''}{settlement.total_soul}`\n"
            f"修为：`{'+' if settlement.total_cultivation > 0 else ''}{format_big_number(settlement.total_cultivation)}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="遗痕变化",
        value=(
            f"杀伐：`{_format_signed_pct(settlement.total_atk_pct)}`\n"
            f"护体：`{_format_signed_pct(settlement.total_def_pct)}`\n"
            f"身法：`{_format_signed_pct(settlement.total_agi_pct)}`"
        ),
        inline=False,
    )
    if settlement.gained_honor_tags:
        embed.add_field(
            name="新得荣誉",
            value="\n".join(f"`{tag}`" for tag in settlement.gained_honor_tags),
            inline=False,
        )
    if settlement.gained_fate_names:
        embed.add_field(
            name="命格异变",
            value="\n".join(f"**{name}**" for name in settlement.gained_fate_names),
            inline=False,
        )
    if settlement.logs:
        lines = [
            f"**【{log.title}】**\n{log.flavor_text}\n**结果：** {log.result_text}"
            for log in settlement.logs[:5]
        ]
        embed.add_field(name="奇遇记录", value="\n\n".join(lines), inline=False)
    return embed


def build_reincarnation_confirm_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 轮回警示",
        description="轮回重修会重置当前主线进度，但可重新抽取命格。此举适合在想赌更强命格时使用。",
        color=discord.Color.red(),
    )
    embed.add_field(
        name="将会失去",
        value=(
            f"当前境界：`{snapshot.realm_display}`\n"
            f"当前修为：`{format_big_number(snapshot.cultivation)}`\n"
            f"当前塔层：`{snapshot.highest_floor}`\n"
            f"当前本命强化：`+{snapshot.artifact_level}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="仍会保留",
        value=(
            f"历史最高塔层：`{snapshot.historical_highest_floor}`\n"
            f"历史最高论道：`#{snapshot.best_ladder_rank}`\n"
            f"轮回次数：`{snapshot.reincarnation_count}`"
        ),
        inline=False,
    )
    embed.set_footer(text="请确认是否真的要舍弃当前主线进度。")
    return embed


def build_reincarnation_embed(snapshot: CharacterSnapshot, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 轮回重修",
        description=message,
        color=RARITY_COLORS[snapshot.fate_rarity],
    )
    embed.add_field(
        name="新命格",
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


def build_fate_rewrite_confirm_embed(snapshot: CharacterSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 逆天改命",
        description="改命不会清空主线进度，但会直接消耗 100 点气运重抽当前命格。",
        color=discord.Color.dark_teal(),
    )
    embed.add_field(
        name="当前命格",
        value=f"`{RARITY_BADGES[snapshot.fate_rarity]}` **{snapshot.fate_name}** · {snapshot.fate_summary}",
        inline=False,
    )
    embed.add_field(
        name="当前气运",
        value=(
            f"气运：`{snapshot.luck}`\n"
            f"本次消耗：`100`\n"
            f"可改命次数：`{snapshot.rewrite_chances}`"
        ),
        inline=False,
    )
    embed.set_footer(text="新命格不会与当前命格重复，但仍可能与历史命格重复。")
    return embed


def build_fate_rewrite_embed(snapshot: CharacterSnapshot, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 改命结果",
        description=message,
        color=RARITY_COLORS[snapshot.fate_rarity],
    )
    embed.add_field(
        name="新命格",
        value=f"`{RARITY_BADGES[snapshot.fate_rarity]}` **{snapshot.fate_name}** · {snapshot.fate_summary}",
        inline=False,
    )
    embed.add_field(
        name="气运余数",
        value=f"当前气运：`{snapshot.luck}` · 可继续改命 `×{snapshot.rewrite_chances}`",
        inline=False,
    )
    return embed


def build_faction_embed(
    snapshot: CharacterSnapshot,
    *,
    target_count: int = 0,
    robbery_status_text: str | None = None,
    bounty_status_text: str | None = None,
) -> discord.Embed:
    if snapshot.faction_key == "righteous":
        description = "你已入正道，可查看悬赏榜并承赏讨伐魔修。"
    elif snapshot.faction_key == "demonic":
        description = "你已堕入魔道，可择人劫掠，但也会不断推高自身悬赏。"
    else:
        description = "你仍处中立，可在此选择投入正道或堕入魔道。"
    embed = discord.Embed(
        title=f"{snapshot.player_name} · 阵营",
        description=description,
        color=discord.Color.dark_gold() if snapshot.faction_key == "righteous" else (discord.Color.dark_red() if snapshot.faction_key == "demonic" else discord.Color.blurple()),
    )
    status_lines = [f"当前阵营：**{snapshot.faction_name}**"]
    if snapshot.faction_title:
        status_lines.append(f"阵营称号：**{snapshot.faction_title}**")
    status_lines.append(f"气运：`{snapshot.luck}` · 可改命 `×{snapshot.rewrite_chances}`")
    if snapshot.faction_key == "righteous":
        status_lines.append(f"善名：`{snapshot.virtue}`")
        status_lines.append(f"讨伐状态：`{bounty_status_text or '可出手'}`")
    elif snapshot.faction_key == "demonic":
        status_lines.append(f"恶名：`{snapshot.infamy}`")
        status_lines.append(f"当前悬赏：`{snapshot.bounty_soul}`")
        status_lines.append(f"劫掠状态：`{robbery_status_text or '可出手'}`")
    embed.add_field(name="阵营状态", value="\n".join(status_lines), inline=False)
    if snapshot.faction_key == "righteous":
        embed.add_field(name="可见悬赏目标", value=f"当前可选目标：`{target_count}`", inline=False)
    elif snapshot.faction_key == "demonic":
        embed.add_field(name="可劫掠目标", value=f"当前可选目标：`{target_count}`", inline=False)
    else:
        embed.add_field(name="当前说明", value="阵营一旦选择，此版本内不可更改。", inline=False)
    return embed


def build_faction_action_embed(snapshot: CharacterSnapshot, title: str, message: str, lines: list[str], *, success: bool) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.player_name} · {title}",
        description=message,
        color=discord.Color.green() if success else discord.Color.orange(),
    )
    embed.add_field(name="当前阵营", value=f"**{snapshot.faction_name}**{f' · {snapshot.faction_title}' if snapshot.faction_title else ''}", inline=False)
    if lines:
        embed.add_field(name="本次结果", value="\n".join(lines), inline=False)
    embed.add_field(
        name="阵营结算",
        value=(
            f"气运：`{snapshot.luck}`\n"
            f"善名：`{snapshot.virtue}`\n"
            f"恶名：`{snapshot.infamy}`\n"
            f"悬赏：`{snapshot.bounty_soul}`"
        ),
        inline=False,
    )
    return embed


def _ladder_hp_after_round(challenger: CharacterSnapshot, defender: CharacterSnapshot, battle, round_no: int) -> tuple[int, int]:
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


def _format_battle_log_line(action, *, include_round: bool) -> str:
    prefix = f"第 {action.round_no} 回合 | " if include_round else ""
    if action.text:
        return f"{prefix}{action.text}"
    if action.dodged:
        return f"{prefix}{action.actor_name} 一击落空，被 {action.target_name} 避开。"
    critical = "暴击" if action.critical else "命中"
    return f"{prefix}{action.actor_name} {critical} {action.target_name}，造成 {format_big_number(action.damage)} 点伤害。"


def _ladder_round_report(battle, round_no: int) -> str:
    lines = []
    for action in battle.logs:
        if action.round_no != round_no:
            continue
        lines.append(_format_battle_log_line(action, include_round=False))
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
        + ("💥 已败退" if challenger_hp <= 0 else f"❤ 血量：`{format_big_number(challenger_hp)} / {format_big_number(battle.challenger_max_hp if battle else challenger.total_def * 10)}`")
    )
    defender_state = (
        f"**{defender.player_name}** · {defender.realm_display}\n"
        f"称号：**{defender.title}**\n"
        + ("💥 已落败" if defender_hp <= 0 else f"❤ 血量：`{format_big_number(defender_hp)} / {format_big_number(battle.defender_max_hp if battle else defender.total_def * 10)}`")
    )
    embed.add_field(name="🧑 你", value=challenger_state, inline=True)
    embed.add_field(name="👤 对手", value=defender_state, inline=True)
    if preview:
        embed.add_field(name="📜 战报", value=report_text, inline=False)
    else:
        embed.add_field(name=f"📜 第 {round_no} 回合", value=report_text, inline=False)
    if final:
        embed.add_field(
            name="✅ 结果",
            value=(
                f"你：`#{result.challenger_rank_before} -> #{result.challenger_rank_after}`\n"
                f"对手：`#{result.defender_rank_before} -> #{result.defender_rank_after}`\n"
                f"剩余挑战：`{result.remaining_attempts}` 次\n"
                f"结果：{result.message}"
            ),
            inline=False,
        )
    return embed


def build_ladder_battle_embed(challenger: CharacterSnapshot, defender: CharacterSnapshot, result: LadderChallengeResult) -> discord.Embed:
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
        line = _format_battle_log_line(action, include_round=True)
        if not action.dodged:
            line = f"{line[:-1]}，余血 {format_big_number(action.target_hp_after)}。"
        lines.append(line)
    if battle.reached_round_limit:
        if mode == "ladder":
            lines.append(f"战至 {MAX_BATTLE_ROUNDS} 回合上限，挑战方未能夺位。")
        elif mode == "bounty":
            lines.append(f"战至 {MAX_BATTLE_ROUNDS} 回合上限，此番讨伐未能得手。")
        elif mode == "robbery":
            lines.append(f"战至 {MAX_BATTLE_ROUNDS} 回合上限，此番劫掠未能得手。")
        else:
            lines.append(f"战至 {MAX_BATTLE_ROUNDS} 回合上限，此层未能踏破。")
    return "\n".join(lines) if lines else "此战过于短促，未留战痕。"


def _format_signed_pct(value: int) -> str:
    return f"{value:+d}%"
