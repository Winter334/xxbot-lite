"""证道战场交互视图。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Awaitable, Callable

import discord
from sqlalchemy import select

from bot.data.proving_ground import (
    PG_BOSS_DISPLAY_NAMES,
    PG_BOSS_PRESET,
    PG_BOSS_SELF,
    PG_BOSS_STRONGEST,
    PG_INVEST_AFFIX_COSTS,
    PG_INVEST_AFFIX_SLOT_MAX,
    PG_INVEST_SPIRIT_COST,
    PG_INVEST_STAT_BOOST_MAX_LEVEL,
    PG_INVEST_STAT_BOOST_PCT_PER_LEVEL,
    PG_INVEST_STAT_COSTS,
    PG_NODE_TYPE_EVENT,
    PG_STATUS_RUNNING,
)
from bot.models.proving_ground_run import ProvingGroundRun
from bot.services.proving_ground_service import (
    MAX_AFFIX_SLOTS,
    PGBuild,
    PGMap,
    ProvingGroundService,
)
from bot.ui.proving_ground import (
    build_pg_affix_enhance_embed,
    build_pg_affix_menu_embed,
    build_pg_affix_pick_embed,
    build_pg_affix_replace_pick_embed,
    build_pg_affix_replace_slot_embed,
    build_pg_build_embed,
    build_pg_combat_result_embed,
    build_pg_entry_embed,
    build_pg_event_embed,
    build_pg_map_embed,
    build_pg_recovery_embed,
    build_pg_settlement_embed,
    build_pg_spirit_menu_embed,
)

if TYPE_CHECKING:
    from bot.main import XianBot


def _info_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.orange())


# ---------------------------------------------------------------------------
# DB 辅助
# ---------------------------------------------------------------------------


async def _get_active_run(session, character_id: int) -> ProvingGroundRun | None:
    """查询角色进行中的证道运行。"""
    stmt = (
        select(ProvingGroundRun)
        .where(ProvingGroundRun.character_id == character_id)
        .where(ProvingGroundRun.status == PG_STATUS_RUNNING)
        .order_by(ProvingGroundRun.id.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def _load_char(bot: XianBot, session, user_id: int, display_name: str):
    """加载角色（get_or_create + 同步资源）。"""
    creation = await bot.character_service.get_or_create_character(session, user_id, display_name)
    character = creation.character
    bot.idle_service.recover_qi(character)
    bot.character_service.refresh_combat_power(character)
    return character


# ---------------------------------------------------------------------------
# 编排函数：入口
# ---------------------------------------------------------------------------


async def build_pg_entry_message(
    bot: XianBot,
    user_id: int,
    display_name: str,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """证道战场主入口。有进行中运行 → 恢复提示；否则 → 入口面板。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)

        # 检查进行中的运行
        run = await _get_active_run(session, character.id)
        if run is not None:
            if ProvingGroundService.is_run_expired(run):
                run.status = "expired"
                await session.commit()
            else:
                summary = ProvingGroundService.get_run_summary(run)
                embed = build_pg_recovery_embed(summary)
                view = ProvingGroundRecoveryView(user_id, run_id=run.id)
                await session.commit()
                return embed, view

        # 无进行中 → 入口面板
        embed = build_pg_entry_embed(
            qi_current=character.current_qi,
            pg_completions=character.pg_completions or 0,
            pg_best_score=character.pg_best_score or 0,
        )
        view = ProvingGroundEntryView(user_id)
        await session.commit()
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：进入战场
# ---------------------------------------------------------------------------


async def _do_enter(bot: XianBot, user_id: int, display_name: str) -> tuple[discord.Embed, discord.ui.View | None]:
    """执行进入证道战场。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        pg_svc: ProvingGroundService = bot.proving_ground_service

        result = pg_svc.enter_proving_ground(character)
        if not result.success:
            embed = _info_embed("🏛️ 无法进入", result.message)
            await session.commit()
            return embed, None

        run = result.run
        assert run is not None
        assert result.pg_map is not None

        # 生成 BOSS 快照
        if run.boss_type == PG_BOSS_STRONGEST:
            # 全服最强快照
            chars = await bot.character_service.list_characters(session)
            if chars:
                strongest = max(chars, key=lambda c: bot.character_service.calculate_total_stats(c).combat_power)
                _, snap = pg_svc.generate_boss_from_character(strongest, bot.character_service, "道心投影")
                run.boss_snapshot_json = json.dumps({"name": snap.name, "atk": snap.atk, "defense": snap.defense, "agility": snap.agility, "max_hp": snap.max_hp})
        elif run.boss_type == PG_BOSS_SELF:
            _, snap = pg_svc.generate_boss_from_character(character, bot.character_service, "心魔映射")
            run.boss_snapshot_json = json.dumps({"name": snap.name, "atk": snap.atk, "defense": snap.defense, "agility": snap.agility, "max_hp": snap.max_hp})
        elif run.boss_type == PG_BOSS_PRESET:
            _, snap = pg_svc.generate_boss_preset()
            run.boss_snapshot_json = json.dumps({"name": snap.name, "atk": snap.atk, "defense": snap.defense, "agility": snap.agility, "max_hp": snap.max_hp})

        session.add(run)
        await session.commit()

        # 显示地图（起始节点 → 选择第一步）
        embed = build_pg_map_embed(
            result.pg_map,
            current_node_id=0,
            visited_node_ids={0},
            score=0,
            pending_affix_ops=0,
            pending_spirit_ops=0,
        )
        view = ProvingGroundMapView(user_id, run_id=run.id)
        start_node = result.pg_map.get_node(0)
        if start_node and start_node.connections:
            view.set_node_buttons(start_node.connections, result.pg_map)
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：推进节点
# ---------------------------------------------------------------------------


async def _do_advance(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    target_node_id: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """推进到目标节点。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "未找到对应的证道运行。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service

        # 准备 BOSS 快照
        boss_snapshot = None
        if run.boss_snapshot_json and run.boss_snapshot_json != "{}":
            try:
                snap_data = json.loads(run.boss_snapshot_json)
                from bot.services.combat_service import CombatantSnapshot
                boss_snapshot = CombatantSnapshot(
                    name=snap_data["name"],
                    atk=snap_data["atk"],
                    defense=snap_data["defense"],
                    agility=snap_data["agility"],
                    max_hp=snap_data["max_hp"],
                )
            except (json.JSONDecodeError, KeyError):
                pass

        result = pg_svc.advance_to_node(
            run, target_node_id, character,
            display_name,
            boss_snapshot=boss_snapshot,
        )
        await session.commit()

    if not result.success:
        return _info_embed("🏛️ 无法前进", result.message), None

    # 事件节点 → 事件界面
    if result.node_type == PG_NODE_TYPE_EVENT and result.event is not None:
        embed = build_pg_event_embed(result.event)
        view = ProvingGroundEventView(user_id, run_id=run_id, event=result.event)
        return embed, view

    # 战斗节点 → 战斗结果
    build = pg_svc.deserialize_build(run.build_json)
    embed = build_pg_combat_result_embed(result, build)

    if result.run_ended:
        # 结算
        settlement = await _do_settle(bot, user_id, display_name, run_id)
        if settlement is not None:
            return settlement
        return embed, None

    view = ProvingGroundPostCombatView(user_id, run_id=run_id)
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：结算
# ---------------------------------------------------------------------------


async def _do_settle(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
) -> tuple[discord.Embed, discord.ui.View | None] | None:
    """结算运行。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        settlement = pg_svc.settle_run(run, character)
        build = pg_svc.deserialize_build(run.build_json)
        await session.commit()

    embed = build_pg_settlement_embed(settlement, build)
    return embed, None


# ---------------------------------------------------------------------------
# 编排函数：操作（词条/器灵）
# ---------------------------------------------------------------------------


async def _do_affix_pick(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """展示词条 3 选 1。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None
        if run.pending_affix_ops <= 0:
            return _info_embed("🏛️ 无操作", "没有剩余的词条操作次数。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)
        choices = pg_svc.roll_affix_choices(3)
        await session.commit()

    embed = build_pg_affix_pick_embed(choices, len(build.affixes), MAX_AFFIX_SLOTS)
    view = ProvingGroundAffixPickView(user_id, run_id=run_id, choices=choices)
    return embed, view


async def _apply_affix_choice(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    choice_index: int,
    choices: list,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """应用词条选择。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)
        entry = choices[choice_index]
        msg = pg_svc.apply_affix_pick(build, entry)
        run.build_json = pg_svc.serialize_build(build)
        run.pending_affix_ops = max(0, run.pending_affix_ops - 1)
        await session.commit()

    return await _show_map(bot, user_id, display_name, run_id)


async def _do_affix_enhance(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    slot: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """强化指定槽位的词条。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)
        msg, consumed = pg_svc.reroll_affix(build, slot)
        if consumed:
            run.build_json = pg_svc.serialize_build(build)
            run.pending_affix_ops = max(0, run.pending_affix_ops - 1)
        await session.commit()

    return await _show_map(bot, user_id, display_name, run_id)


async def _do_affix_replace(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    new_entry,
    replace_slot: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """用新词条替换指定槽位。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)

        from bot.data.artifact_affixes import ArtifactAffixEntry
        if 0 <= replace_slot < len(build.affixes):
            build.affixes[replace_slot] = ArtifactAffixEntry(
                slot=replace_slot,
                affix_id=new_entry.affix_id,
                rolls=new_entry.rolls,
            )
        run.build_json = pg_svc.serialize_build(build)
        # 操作次数已在选择新词条时扣除，此处不再扣除
        await session.commit()

    return await _show_map(bot, user_id, display_name, run_id)


async def _do_affix_abandon(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """放弃词条操作（消耗操作次数但不做任何改变）。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None
        run.pending_affix_ops = max(0, run.pending_affix_ops - 1)
        await session.commit()

    return await _show_map(bot, user_id, display_name, run_id)


async def _do_spirit_op(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    action: str,  # "roll" or "reroll"
) -> tuple[discord.Embed, discord.ui.View | None]:
    """执行器灵操作。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None
        if run.pending_spirit_ops <= 0:
            return _info_embed("🏛️ 无操作", "没有剩余的器灵操作次数。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)

        if action == "roll":
            msg, _ = pg_svc.roll_new_spirit(build)
            consumed = True
        else:
            msg, consumed = pg_svc.reroll_spirit(build)

        if consumed:
            run.build_json = pg_svc.serialize_build(build)
            run.pending_spirit_ops = max(0, run.pending_spirit_ops - 1)
        await session.commit()

    return await _show_map(bot, user_id, display_name, run_id)


# ---------------------------------------------------------------------------
# 编排函数：永久投资
# ---------------------------------------------------------------------------


async def _do_invest(
    bot: XianBot,
    user_id: int,
    display_name: str,
    invest_type: str,  # "stat" / "affix" / "spirit"
) -> tuple[discord.Embed, discord.ui.View | None]:
    """执行永久投资操作，刷新入口面板。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        pg_svc: ProvingGroundService = bot.proving_ground_service

        if invest_type == "stat":
            ok, msg = pg_svc.invest_stat_boost(character)
        elif invest_type == "affix":
            ok, msg = pg_svc.invest_starter_affix(character)
        elif invest_type == "spirit":
            ok, msg = pg_svc.invest_starter_spirit(character)
        else:
            ok, msg = False, "未知投资类型。"
        await session.commit()

    # 无论成功失败都刷新入口面板（显示最新状态 + 提示信息）
    return await _build_invest_panel(bot, user_id, display_name, feedback=msg)


async def _build_invest_panel(
    bot: XianBot,
    user_id: int,
    display_name: str,
    *,
    feedback: str = "",
) -> tuple[discord.Embed, discord.ui.View | None]:
    """构建永久投资面板。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        await session.commit()

    stat_level = character.pg_invest_stat_level
    affix_slots = character.pg_invest_affix_slots
    spirit_unlocked = character.pg_invest_spirit_unlocked
    lingshi = character.lingshi

    lines: list[str] = []
    if feedback:
        lines.append(f"💬 {feedback}\n")

    # 体魄强化
    total_stat_pct = stat_level * PG_INVEST_STAT_BOOST_PCT_PER_LEVEL
    if stat_level >= PG_INVEST_STAT_BOOST_MAX_LEVEL:
        lines.append(f"**强化体魄** ── Lv.{stat_level} (三维 +{total_stat_pct}%) ✅ 已满级")
    else:
        next_cost = PG_INVEST_STAT_COSTS[stat_level]
        lines.append(
            f"**强化体魄** ── Lv.{stat_level} → Lv.{stat_level + 1}"
            f"  (三维 +{total_stat_pct}% → +{total_stat_pct + PG_INVEST_STAT_BOOST_PCT_PER_LEVEL}%)"
            f"  | 费用: {next_cost:,} 灵石"
        )

    # 初始词条
    if affix_slots >= PG_INVEST_AFFIX_SLOT_MAX:
        lines.append(f"**初始词条** ── {affix_slots}/{PG_INVEST_AFFIX_SLOT_MAX} 槽位 ✅ 已全部解锁")
    else:
        next_cost = PG_INVEST_AFFIX_COSTS[affix_slots]
        lines.append(
            f"**初始词条** ── {affix_slots}/{PG_INVEST_AFFIX_SLOT_MAX} 槽位"
            f"  | 解锁下一槽: {next_cost:,} 灵石"
        )

    # 初始器灵
    if spirit_unlocked:
        lines.append("**初始器灵** ── ✅ 已解锁")
    else:
        lines.append(f"**初始器灵** ── 未解锁  | 费用: {PG_INVEST_SPIRIT_COST:,} 灵石")

    lines.append(f"\n💰 当前灵石: **{lingshi:,}**")

    embed = discord.Embed(
        title="🏛️ 证道战场 · 局外强化",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    view = ProvingGroundInvestView(user_id, stat_level, affix_slots, spirit_unlocked)
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：事件
# ---------------------------------------------------------------------------


async def _do_event_choice(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
    event,
    choice_id: str,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """应用事件选择。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)
        result = pg_svc.apply_event(event, choice_id, build, run, character)
        run.build_json = pg_svc.serialize_build(build)
        await session.commit()

    embed = build_pg_event_embed(event, result=result)
    view = ProvingGroundPostEventView(user_id, run_id=run_id)
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：显示地图（通用）
# ---------------------------------------------------------------------------


async def _show_map(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """加载当前地图并展示。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None
        if run.status != PG_STATUS_RUNNING:
            return _info_embed("🏛️ 已结束", "本次证道之行已结束。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        pg_map = pg_svc.deserialize_map(run.map_json)
        # visited = 所有 id <= current_node_id 的起始部分（简化：用节点 layer <= 当前 layer）
        current_node = pg_map.get_node(run.current_node_id)
        visited = set()
        if current_node:
            for n in pg_map.nodes:
                if n.layer <= current_node.layer:
                    visited.add(n.node_id)

        embed = build_pg_map_embed(
            pg_map,
            current_node_id=run.current_node_id,
            visited_node_ids=visited,
            score=run.score,
            pending_affix_ops=run.pending_affix_ops,
            pending_spirit_ops=run.pending_spirit_ops,
        )
        view = ProvingGroundMapView(user_id, run_id=run_id)
        if current_node and current_node.connections:
            view.set_node_buttons(current_node.connections, pg_map)
        await session.commit()
    return embed, view


# ---------------------------------------------------------------------------
# 编排函数：查看构筑
# ---------------------------------------------------------------------------


async def _show_build(
    bot: XianBot,
    user_id: int,
    display_name: str,
    run_id: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """查看当前构筑。"""
    async with bot.session_factory() as session:
        character = await _load_char(bot, session, user_id, display_name)
        run = await session.get(ProvingGroundRun, run_id)
        if run is None or run.character_id != character.id:
            return _info_embed("🏛️ 错误", "运行不存在。"), None

        pg_svc: ProvingGroundService = bot.proving_ground_service
        build = pg_svc.deserialize_build(run.build_json)
        await session.commit()

    embed = build_pg_build_embed(build, run.pending_affix_ops, run.pending_spirit_ops)
    view = ProvingGroundBuildView(user_id, run_id=run_id)
    return embed, view


# ===========================================================================
# View 基类
# ===========================================================================


class _PGBaseView(discord.ui.View):
    """证道战场 View 基类，owner 锁定 + run_id 记忆。"""

    def __init__(self, owner_user_id: int, *, run_id: int, timeout: float | None = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_user_id = owner_user_id
        self.run_id = run_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message("这张面板并非为你而开。", ephemeral=True)
        return False

    async def _edit(self, interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View | None) -> None:
        await interaction.response.edit_message(embed=embed, view=view)


# ===========================================================================
# View: 入口面板
# ===========================================================================


class ProvingGroundEntryView(discord.ui.View):
    """证道战场入口（无 run_id）。"""

    def __init__(self, owner_user_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_user_id = owner_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message("这张面板并非为你而开。", ephemeral=True)
        return False

    @discord.ui.button(label="踏入证道", style=discord.ButtonStyle.primary, emoji="🏛️")
    async def enter_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view = await _do_enter(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="局外强化", style=discord.ButtonStyle.secondary, emoji="💎")
    async def invest_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: XianBot = interaction.client  # type: ignore[assignment]
        embed, view = await _build_invest_panel(bot, interaction.user.id, interaction.user.display_name)
        await interaction.response.edit_message(embed=embed, view=view)


# ===========================================================================
# View: 恢复提示
# ===========================================================================


class ProvingGroundRecoveryView(_PGBaseView):
    """有进行中运行时的恢复/放弃选择。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self._add_buttons()

    def _add_buttons(self) -> None:
        resume_btn = discord.ui.Button(label="继续证道", style=discord.ButtonStyle.primary, emoji="▶️")
        abandon_btn = discord.ui.Button(label="放弃本次", style=discord.ButtonStyle.danger, emoji="🗑️")

        async def on_resume(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        async def on_abandon(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            async with bot.session_factory() as session:
                run = await session.get(ProvingGroundRun, self.run_id)
                if run is not None and run.status == PG_STATUS_RUNNING:
                    run.status = "expired"
                    await session.commit()
            embed = _info_embed("🏛️ 放弃", "本次证道之行已放弃。")
            await interaction.response.edit_message(embed=embed, view=None)

        resume_btn.callback = on_resume
        abandon_btn.callback = on_abandon
        self.add_item(resume_btn)
        self.add_item(abandon_btn)


# ===========================================================================
# View: 地图导航
# ===========================================================================


class ProvingGroundMapView(_PGBaseView):
    """地图节点选择。按钮在每次创建时动态加载。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        # 按钮由 _refresh_buttons 在 build 后填充
        self._add_static_buttons()

    def _add_static_buttons(self) -> None:
        # 查看构筑
        build_btn = discord.ui.Button(label="查看构筑", style=discord.ButtonStyle.secondary, emoji="📋", row=1)

        async def on_build(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_build(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        build_btn.callback = on_build
        self.add_item(build_btn)

        # 词条操作
        affix_btn = discord.ui.Button(label="词条操作", style=discord.ButtonStyle.secondary, emoji="📿", row=1)

        async def on_affix(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            async with bot.session_factory() as session:
                run = await session.get(ProvingGroundRun, self.run_id)
                if run is None:
                    await interaction.response.send_message("运行不存在。", ephemeral=True)
                    return
                if run.pending_affix_ops <= 0:
                    await interaction.response.send_message("没有剩余的词条操作次数。", ephemeral=True)
                    return
                build = bot.proving_ground_service.deserialize_build(run.build_json)
            # 无词条且有空槽位：直接进入 3 选 1
            if not build.affixes:
                embed, view = await _do_affix_pick(
                    bot, interaction.user.id, interaction.user.display_name, self.run_id,
                )
                await interaction.response.edit_message(embed=embed, view=view)
                return
            # 有词条：展示操作菜单（抽取/替换/强化）
            embed = build_pg_affix_menu_embed(build, run.pending_affix_ops)
            view = ProvingGroundAffixMenuView(
                interaction.user.id, run_id=self.run_id, build=build,
            )
            await interaction.response.edit_message(embed=embed, view=view)

        affix_btn.callback = on_affix
        self.add_item(affix_btn)

        # 器灵操作
        spirit_btn = discord.ui.Button(label="器灵操作", style=discord.ButtonStyle.secondary, emoji="🔮", row=1)

        async def on_spirit(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            async with bot.session_factory() as session:
                run = await session.get(ProvingGroundRun, self.run_id)
                if run is None:
                    await interaction.response.send_message("运行不存在。", ephemeral=True)
                    return
                if run.pending_spirit_ops <= 0:
                    await interaction.response.send_message("没有剩余的器灵操作次数。", ephemeral=True)
                    return
                build = bot.proving_ground_service.deserialize_build(run.build_json)
            # 无器灵时直接 roll，不需要选择
            if build.spirit_power is None:
                embed, view = await _do_spirit_op(
                    bot, interaction.user.id, interaction.user.display_name, self.run_id, "roll",
                )
                await interaction.response.edit_message(embed=embed, view=view)
                return
            # 有器灵时展示选择面板
            embed = build_pg_spirit_menu_embed(build, run.pending_spirit_ops)
            view = ProvingGroundSpiritMenuView(interaction.user.id, run_id=self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        spirit_btn.callback = on_spirit
        self.add_item(spirit_btn)

    def set_node_buttons(self, connections: tuple[int, ...], pg_map: PGMap) -> None:
        """根据当前节点的连接动态添加路径选择按钮。"""
        from bot.data.proving_ground import (
            PG_NODE_TYPE_BOSS,
            PG_NODE_TYPE_ELITE,
            PG_NODE_TYPE_EVENT,
            PG_NODE_TYPE_NORMAL,
        )
        _EMOJI = {
            PG_NODE_TYPE_NORMAL: "⚔️",
            PG_NODE_TYPE_ELITE: "💀",
            PG_NODE_TYPE_EVENT: "❓",
            PG_NODE_TYPE_BOSS: "👹",
        }
        _LABEL = {
            PG_NODE_TYPE_NORMAL: "普通",
            PG_NODE_TYPE_ELITE: "精英",
            PG_NODE_TYPE_EVENT: "事件",
            PG_NODE_TYPE_BOSS: "BOSS",
        }
        for i, nid in enumerate(connections):
            node = pg_map.get_node(nid)
            if node is None:
                continue
            emoji = _EMOJI.get(node.node_type, "❔")
            label = f"路径 {i + 1}: {_LABEL.get(node.node_type, '未知')}"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, emoji=emoji, row=0)

            async def on_advance(interaction: discord.Interaction, target_id: int = nid) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_advance(
                    bot, interaction.user.id, interaction.user.display_name,
                    self.run_id, target_id,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_advance
            self.add_item(btn)


# ===========================================================================
# View: 战斗后（继续/操作）
# ===========================================================================


class ProvingGroundPostCombatView(_PGBaseView):
    """战斗结束后的继续按钮。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self._add_buttons()

    def _add_buttons(self) -> None:
        continue_btn = discord.ui.Button(label="继续前进", style=discord.ButtonStyle.primary, emoji="▶️")

        async def on_continue(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        continue_btn.callback = on_continue
        self.add_item(continue_btn)


# ===========================================================================
# View: 事件选择
# ===========================================================================


class ProvingGroundEventView(_PGBaseView):
    """事件选项按钮。"""

    def __init__(self, owner_user_id: int, *, run_id: int, event) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self.event = event
        self._add_buttons()

    def _add_buttons(self) -> None:
        if self.event.auto_apply:
            # 自动生效事件只需一个确认按钮
            btn = discord.ui.Button(label="确认", style=discord.ButtonStyle.primary)

            async def on_auto(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_event_choice(
                    bot, interaction.user.id, interaction.user.display_name,
                    self.run_id, self.event, "",
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_auto
            self.add_item(btn)
        elif self.event.choices:
            for choice in self.event.choices:
                style = discord.ButtonStyle.danger if choice.risk else discord.ButtonStyle.primary
                btn = discord.ui.Button(label=choice.label, style=style)

                async def on_choice(interaction: discord.Interaction, cid: str = choice.choice_id) -> None:
                    bot: XianBot = interaction.client  # type: ignore[assignment]
                    embed, view = await _do_event_choice(
                        bot, interaction.user.id, interaction.user.display_name,
                        self.run_id, self.event, cid,
                    )
                    await interaction.response.edit_message(embed=embed, view=view)

                btn.callback = on_choice
                self.add_item(btn)

        # 跳过按钮（对有选择的事件也提供跳过）
        if not self.event.auto_apply:
            skip_btn = discord.ui.Button(label="跳过", style=discord.ButtonStyle.secondary, emoji="⏭️")

            async def on_skip(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
                await interaction.response.edit_message(embed=embed, view=view)

            skip_btn.callback = on_skip
            self.add_item(skip_btn)


# ===========================================================================
# View: 事件结果后
# ===========================================================================


class ProvingGroundPostEventView(_PGBaseView):
    """事件结果后继续。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        btn = discord.ui.Button(label="继续前进", style=discord.ButtonStyle.primary, emoji="▶️")

        async def on_continue(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        btn.callback = on_continue
        self.add_item(btn)


# ===========================================================================
# View: 词条 3 选 1
# ===========================================================================


class ProvingGroundAffixPickView(_PGBaseView):
    """词条 3 选 1 界面。"""

    def __init__(self, owner_user_id: int, *, run_id: int, choices: list) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self.choices = choices
        self._add_buttons()

    def _add_buttons(self) -> None:
        for i, affix in enumerate(self.choices):
            label = f"选项 {i + 1}"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

            async def on_pick(interaction: discord.Interaction, idx: int = i) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _apply_affix_choice(
                    bot, interaction.user.id, interaction.user.display_name,
                    self.run_id, idx, self.choices,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_pick
            self.add_item(btn)

        # 返回按钮
        back_btn = discord.ui.Button(label="返回地图", style=discord.ButtonStyle.secondary, emoji="🗺️")

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        back_btn.callback = on_back
        self.add_item(back_btn)


# ===========================================================================
# View: 构筑查看
# ===========================================================================


class ProvingGroundBuildView(_PGBaseView):
    """构筑查看面板。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        btn = discord.ui.Button(label="返回地图", style=discord.ButtonStyle.secondary, emoji="🗺️")

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        btn.callback = on_back
        self.add_item(btn)


# ===========================================================================
# View: 局外强化
# ===========================================================================


class ProvingGroundInvestView(discord.ui.View):
    """永久投资面板（不依赖 run_id）。"""

    def __init__(
        self,
        owner_user_id: int,
        stat_level: int,
        affix_slots: int,
        spirit_unlocked: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_user_id = owner_user_id
        self._add_buttons(stat_level, affix_slots, spirit_unlocked)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message("这张面板并非为你而开。", ephemeral=True)
        return False

    def _add_buttons(self, stat_level: int, affix_slots: int, spirit_unlocked: bool) -> None:
        # 强化体魄
        if stat_level < PG_INVEST_STAT_BOOST_MAX_LEVEL:
            cost = PG_INVEST_STAT_COSTS[stat_level]
            btn = discord.ui.Button(
                label=f"强化体魄 ({cost:,}灵石)",
                style=discord.ButtonStyle.primary,
                emoji="💪",
            )

            async def on_stat(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_invest(bot, interaction.user.id, interaction.user.display_name, "stat")
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_stat
            self.add_item(btn)

        # 解锁词条槽位
        if affix_slots < PG_INVEST_AFFIX_SLOT_MAX:
            cost = PG_INVEST_AFFIX_COSTS[affix_slots]
            btn = discord.ui.Button(
                label=f"解锁词条 ({cost:,}灵石)",
                style=discord.ButtonStyle.primary,
                emoji="📿",
            )

            async def on_affix(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_invest(bot, interaction.user.id, interaction.user.display_name, "affix")
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_affix
            self.add_item(btn)

        # 解锁器灵
        if not spirit_unlocked:
            btn = discord.ui.Button(
                label=f"解锁器灵 ({PG_INVEST_SPIRIT_COST:,}灵石)",
                style=discord.ButtonStyle.primary,
                emoji="🔮",
            )

            async def on_spirit(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_invest(bot, interaction.user.id, interaction.user.display_name, "spirit")
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_spirit
            self.add_item(btn)

        # 返回入口
        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="◀️")

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await build_pg_entry_message(bot, interaction.user.id, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed, view=view)

        back_btn.callback = on_back
        self.add_item(back_btn)


# ===========================================================================
# View: 器灵操作菜单
# ===========================================================================


class ProvingGroundSpiritMenuView(_PGBaseView):
    """器灵操作选择面板：重新抽取 / 强化当前 / 返回。"""

    def __init__(self, owner_user_id: int, *, run_id: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self._add_buttons()

    def _add_buttons(self) -> None:
        # 重新抽取（替换）
        roll_btn = discord.ui.Button(label="重新抽取", style=discord.ButtonStyle.primary, emoji="🆕")

        async def on_roll(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _do_spirit_op(
                bot, interaction.user.id, interaction.user.display_name, self.run_id, "roll",
            )
            await interaction.response.edit_message(embed=embed, view=view)

        roll_btn.callback = on_roll
        self.add_item(roll_btn)

        # 强化当前
        reroll_btn = discord.ui.Button(label="强化当前", style=discord.ButtonStyle.success, emoji="⬆️")

        async def on_reroll(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _do_spirit_op(
                bot, interaction.user.id, interaction.user.display_name, self.run_id, "reroll",
            )
            await interaction.response.edit_message(embed=embed, view=view)

        reroll_btn.callback = on_reroll
        self.add_item(reroll_btn)

        # 返回地图
        back_btn = discord.ui.Button(label="返回地图", style=discord.ButtonStyle.secondary, emoji="🗺️")

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        back_btn.callback = on_back
        self.add_item(back_btn)


# ===========================================================================
# View: 词条操作菜单
# ===========================================================================


class ProvingGroundAffixMenuView(_PGBaseView):
    """词条操作选择面板：抽取新词条 / 替换词条 / 强化词条 / 返回。"""

    def __init__(self, owner_user_id: int, *, run_id: int, build: PGBuild) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self._add_buttons(build)

    def _add_buttons(self, build: PGBuild) -> None:
        # 抽取新词条（有空槽位时）
        if len(build.affixes) < MAX_AFFIX_SLOTS:
            new_btn = discord.ui.Button(label="抽取新词条", style=discord.ButtonStyle.primary, emoji="🆕")

            async def on_new(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_affix_pick(
                    bot, interaction.user.id, interaction.user.display_name, self.run_id,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            new_btn.callback = on_new
            self.add_item(new_btn)

        # 替换词条（满槽时）
        if len(build.affixes) >= MAX_AFFIX_SLOTS:
            replace_btn = discord.ui.Button(label="替换词条", style=discord.ButtonStyle.primary, emoji="🔄")

            async def on_replace(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                async with bot.session_factory() as session:
                    character = await _load_char(bot, session, interaction.user.id, interaction.user.display_name)
                    run = await session.get(ProvingGroundRun, self.run_id)
                    if run is None or run.character_id != character.id:
                        await interaction.response.send_message("运行不存在。", ephemeral=True)
                        return
                    pg_svc: ProvingGroundService = bot.proving_ground_service
                    current_build = pg_svc.deserialize_build(run.build_json)
                    choices = pg_svc.roll_affix_choices(3)
                    # 扣除操作次数
                    run.pending_affix_ops = max(0, run.pending_affix_ops - 1)
                    await session.commit()
                embed = build_pg_affix_replace_pick_embed(choices, current_build)
                view = ProvingGroundAffixReplacePickView(
                    interaction.user.id, run_id=self.run_id, choices=choices, build=current_build,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            replace_btn.callback = on_replace
            self.add_item(replace_btn)

        # 强化词条（有词条时）
        if build.affixes:
            enhance_btn = discord.ui.Button(label="强化词条", style=discord.ButtonStyle.success, emoji="⬆️")

            async def on_enhance(interaction: discord.Interaction) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                async with bot.session_factory() as session:
                    run = await session.get(ProvingGroundRun, self.run_id)
                    if run is None:
                        await interaction.response.send_message("运行不存在。", ephemeral=True)
                        return
                    current_build = bot.proving_ground_service.deserialize_build(run.build_json)
                embed = build_pg_affix_enhance_embed(current_build)
                view = ProvingGroundAffixEnhanceView(
                    interaction.user.id, run_id=self.run_id, affix_count=len(current_build.affixes),
                )
                await interaction.response.edit_message(embed=embed, view=view)

            enhance_btn.callback = on_enhance
            self.add_item(enhance_btn)

        # 返回地图
        back_btn = discord.ui.Button(label="返回地图", style=discord.ButtonStyle.secondary, emoji="🗺️")

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        back_btn.callback = on_back
        self.add_item(back_btn)


# ===========================================================================
# View: 词条强化选择
# ===========================================================================


class ProvingGroundAffixEnhanceView(_PGBaseView):
    """选择要强化的词条。"""

    def __init__(self, owner_user_id: int, *, run_id: int, affix_count: int) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self._add_buttons(affix_count)

    def _add_buttons(self, affix_count: int) -> None:
        for slot in range(affix_count):
            btn = discord.ui.Button(
                label=f"强化词条 {slot + 1}",
                style=discord.ButtonStyle.primary,
                row=0 if slot < 3 else 1,
            )

            async def on_enhance(interaction: discord.Interaction, s: int = slot) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_affix_enhance(
                    bot, interaction.user.id, interaction.user.display_name, self.run_id, s,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_enhance
            self.add_item(btn)

        # 返回
        back_btn = discord.ui.Button(label="返回地图", style=discord.ButtonStyle.secondary, emoji="🗺️", row=2)

        async def on_back(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        back_btn.callback = on_back
        self.add_item(back_btn)


# ===========================================================================
# View: 词条替换 — 3 选 1
# ===========================================================================


class ProvingGroundAffixReplacePickView(_PGBaseView):
    """替换模式：先从 3 个候选中选一个新词条。"""

    def __init__(
        self,
        owner_user_id: int,
        *,
        run_id: int,
        choices: list,
        build: PGBuild,
    ) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self.choices = choices
        self.build = build
        self._add_buttons()

    def _add_buttons(self) -> None:
        for i, affix in enumerate(self.choices):
            btn = discord.ui.Button(label=f"候选 {i + 1}", style=discord.ButtonStyle.primary)

            async def on_pick(interaction: discord.Interaction, idx: int = i) -> None:
                selected = self.choices[idx]
                # 重新读取最新 build
                bot: XianBot = interaction.client  # type: ignore[assignment]
                async with bot.session_factory() as session:
                    run = await session.get(ProvingGroundRun, self.run_id)
                    if run is None:
                        await interaction.response.send_message("运行不存在。", ephemeral=True)
                        return
                    current_build = bot.proving_ground_service.deserialize_build(run.build_json)
                embed = build_pg_affix_replace_slot_embed(selected, current_build)
                view = ProvingGroundAffixReplaceSlotView(
                    interaction.user.id, run_id=self.run_id, new_entry=selected,
                    affix_count=len(current_build.affixes),
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_pick
            self.add_item(btn)

        # 放弃替换
        abandon_btn = discord.ui.Button(label="放弃替换", style=discord.ButtonStyle.danger, emoji="🗑️")

        async def on_abandon(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            # 操作次数已在进入时扣除，直接返回地图
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        abandon_btn.callback = on_abandon
        self.add_item(abandon_btn)


# ===========================================================================
# View: 词条替换 — 选择槽位
# ===========================================================================


class ProvingGroundAffixReplaceSlotView(_PGBaseView):
    """选定新词条后，选择替换哪个旧词条。"""

    def __init__(
        self,
        owner_user_id: int,
        *,
        run_id: int,
        new_entry,
        affix_count: int,
    ) -> None:
        super().__init__(owner_user_id, run_id=run_id)
        self.new_entry = new_entry
        self._add_buttons(affix_count)

    def _add_buttons(self, affix_count: int) -> None:
        for slot in range(affix_count):
            btn = discord.ui.Button(
                label=f"替换槽位 {slot + 1}",
                style=discord.ButtonStyle.primary,
                row=0 if slot < 3 else 1,
            )

            async def on_replace(interaction: discord.Interaction, s: int = slot) -> None:
                bot: XianBot = interaction.client  # type: ignore[assignment]
                embed, view = await _do_affix_replace(
                    bot, interaction.user.id, interaction.user.display_name,
                    self.run_id, self.new_entry, s,
                )
                await interaction.response.edit_message(embed=embed, view=view)

            btn.callback = on_replace
            self.add_item(btn)

        # 放弃替换
        abandon_btn = discord.ui.Button(label="放弃替换", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)

        async def on_abandon(interaction: discord.Interaction) -> None:
            bot: XianBot = interaction.client  # type: ignore[assignment]
            embed, view = await _show_map(bot, interaction.user.id, interaction.user.display_name, self.run_id)
            await interaction.response.edit_message(embed=embed, view=view)

        abandon_btn.callback = on_abandon
        self.add_item(abandon_btn)
