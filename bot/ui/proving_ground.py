"""证道战场 UI — 纯 Embed 构建函数。"""

from __future__ import annotations

import discord

from bot.data.artifact_affixes import ARTIFACT_AFFIXES_BY_ID
from bot.data.proving_ground import (
    PG_BOSS_DISPLAY_NAMES,
    PG_ENTRY_QI_COST,
    PG_MAP_LAYERS,
    PG_NODE_TYPE_BOSS,
    PG_NODE_TYPE_ELITE,
    PG_NODE_TYPE_EVENT,
    PG_NODE_TYPE_NORMAL,
    PG_NODE_TYPE_START,
)
from bot.data.spirits import SPIRIT_POWER_BY_ID
from bot.services.combat_service import CombatService
from bot.services.proving_ground_service import (
    MAX_AFFIX_SLOTS,
    MapNode,
    PGBuild,
    PGEvent,
    PGEventResult,
    PGMap,
    PGNodeResult,
    PGSettlement,
)
from bot.utils.formatters import format_big_number


MAX_BATTLE_ROUNDS = CombatService.max_rounds

# 节点类型 emoji
_NODE_EMOJI: dict[str, str] = {
    PG_NODE_TYPE_START: "🏁",
    PG_NODE_TYPE_NORMAL: "⚔️",
    PG_NODE_TYPE_ELITE: "💀",
    PG_NODE_TYPE_EVENT: "❓",
    PG_NODE_TYPE_BOSS: "👹",
}

# 主题色
_PG_COLOR = discord.Color.dark_teal()
_PG_COLOR_SUCCESS = discord.Color.green()
_PG_COLOR_FAILURE = discord.Color.orange()
_PG_COLOR_EVENT = discord.Color.purple()
_PG_COLOR_SETTLEMENT = discord.Color.gold()


def _affix_display(affix_id: str, rolls: dict[str, int]) -> tuple[str, str]:
    """返回 (中文名, 描述文本)。找不到定义时 fallback 到 affix_id。"""
    defn = ARTIFACT_AFFIXES_BY_ID.get(affix_id)
    if defn is None:
        rolls_text = " / ".join(f"{k}: {v}" for k, v in rolls.items())
        return affix_id, rolls_text
    name = defn.name
    desc = defn.describe(rolls)
    return name, desc


def _spirit_display(power_id: str, rolls: dict[str, int]) -> tuple[str, str]:
    """返回 (中文名, 描述文本)。找不到定义时 fallback 到 power_id。"""
    defn = SPIRIT_POWER_BY_ID.get(power_id)
    if defn is None:
        rolls_text = " / ".join(f"{k}: {v}" for k, v in rolls.items())
        return power_id, rolls_text
    name = defn.name
    desc = defn.describe(rolls)
    return name, desc


# ---------------------------------------------------------------------------
# 入口界面
# ---------------------------------------------------------------------------


def build_pg_entry_embed(
    qi_current: int,
    pg_completions: int,
    pg_best_score: int,
) -> discord.Embed:
    """证道战场入口/介绍界面。"""
    can_enter = qi_current >= PG_ENTRY_QI_COST
    embed = discord.Embed(
        title="🏛️ 证道战场",
        description=(
            "天道试炼之地，一切外物皆被剥离。\n"
            "以裸身面板闯入，沿途拾取词条与器灵，构筑属于你的证道之路。\n\n"
            f"消耗 **{PG_ENTRY_QI_COST} 气机** 进入，{PG_MAP_LAYERS} 层关卡后迎战 BOSS。"
        ),
        color=_PG_COLOR if can_enter else _PG_COLOR_FAILURE,
    )
    embed.add_field(
        name="📊 个人战绩",
        value=(
            f"通关次数：`{pg_completions}`\n"
            f"最高积分：`{pg_best_score}`\n"
            f"当前气机：`{qi_current}` / 需要 `{PG_ENTRY_QI_COST}`"
        ),
        inline=False,
    )
    if not can_enter:
        embed.set_footer(text="气机不足，无法进入证道战场。")
    return embed


# ---------------------------------------------------------------------------
# 恢复提示
# ---------------------------------------------------------------------------


def build_pg_recovery_embed(
    run_summary: dict,
) -> discord.Embed:
    """有进行中运行时的恢复提示界面。"""
    embed = discord.Embed(
        title="🏛️ 证道战场 · 进度恢复",
        description="检测到未完成的证道之行，是否继续？",
        color=_PG_COLOR,
    )
    embed.add_field(
        name="📋 上次进度",
        value=(
            f"当前层数：`{run_summary.get('current_layer', 0)}` / `{run_summary.get('total_layers', PG_MAP_LAYERS + 1)}`\n"
            f"已获积分：`{run_summary.get('score', 0)}`\n"
            f"已装词条：`{run_summary.get('affix_count', 0)}`\n"
            f"器灵：{'✅' if run_summary.get('has_spirit') else '❌'}"
        ),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# 迷雾地图
# ---------------------------------------------------------------------------


def build_pg_map_embed(
    pg_map: PGMap,
    current_node_id: int,
    visited_node_ids: set[int],
    score: int,
    pending_affix_ops: int,
    pending_spirit_ops: int,
) -> discord.Embed:
    """迷雾地图 — 只显示当前节点 + 下一层可选节点。"""
    current_node = pg_map.get_node(current_node_id)
    if current_node is None:
        return discord.Embed(title="🏛️ 证道战场", description="地图数据异常。", color=_PG_COLOR_FAILURE)

    embed = discord.Embed(
        title="🏛️ 证道战场 · 选择路径",
        color=_PG_COLOR,
    )

    # 当前位置
    current_emoji = _NODE_EMOJI.get(current_node.node_type, "📍")
    embed.description = f"当前位置：{current_emoji} **第 {current_node.layer} 层**　积分：`{score}`"

    # 可选下一步节点
    if current_node.connections:
        next_lines: list[str] = []
        for i, nid in enumerate(current_node.connections, start=1):
            next_node = pg_map.get_node(nid)
            if next_node is None:
                continue
            emoji = _NODE_EMOJI.get(next_node.node_type, "❔")
            label = _node_type_label(next_node.node_type)
            visited_mark = " ✅" if nid in visited_node_ids else ""
            next_lines.append(f"{emoji} **路径 {i}**：{label} (第 {next_node.layer} 层){visited_mark}")
        embed.add_field(
            name="🔮 前方路径",
            value="\n".join(next_lines) if next_lines else "无可用路径。",
            inline=False,
        )

    # 操作次数提示
    ops_parts: list[str] = []
    if pending_affix_ops > 0:
        ops_parts.append(f"词条操作 ×{pending_affix_ops}")
    if pending_spirit_ops > 0:
        ops_parts.append(f"器灵操作 ×{pending_spirit_ops}")
    if ops_parts:
        embed.set_footer(text="待使用：" + "　".join(ops_parts))

    return embed


def _node_type_label(node_type: str) -> str:
    labels = {
        PG_NODE_TYPE_NORMAL: "普通敌人",
        PG_NODE_TYPE_ELITE: "精英敌人",
        PG_NODE_TYPE_EVENT: "未知事件",
        PG_NODE_TYPE_BOSS: "BOSS",
        PG_NODE_TYPE_START: "起点",
    }
    return labels.get(node_type, "未知")


# ---------------------------------------------------------------------------
# 战斗结果
# ---------------------------------------------------------------------------


def build_pg_combat_result_embed(
    result: PGNodeResult,
    build: PGBuild,
) -> discord.Embed:
    """节点战斗结果展示。"""
    if result.victory:
        embed = discord.Embed(
            title=f"⚔️ 击败 {result.enemy_name}",
            color=_PG_COLOR_SUCCESS,
        )
    else:
        embed = discord.Embed(
            title=f"💀 败于 {result.enemy_name}",
            color=_PG_COLOR_FAILURE,
        )

    # 战报摘要
    if result.battle is not None:
        excerpt = _battle_excerpt(result.battle, 6)
        embed.add_field(name="📜 战斗经过", value=excerpt, inline=False)

    # 奖励
    reward_lines: list[str] = []
    if result.score_gained > 0:
        reward_lines.append(f"积分 +{result.score_gained}")
    if result.affix_ops_gained > 0:
        reward_lines.append(f"词条操作 +{result.affix_ops_gained}")
    if result.spirit_ops_gained > 0:
        reward_lines.append(f"器灵操作 +{result.spirit_ops_gained}")
    if reward_lines:
        embed.add_field(name="🎁 战利品", value="\n".join(reward_lines), inline=False)

    if result.run_ended:
        embed.set_footer(text="本次证道之行已结束。")

    return embed


def _format_log_entry(entry) -> str:
    """将单条 ActionLog 格式化为可读文本。"""
    prefix = f"R{entry.round_no}"
    if entry.text:
        return f"{prefix} {entry.text}"
    if entry.dodged:
        return f"{prefix} {entry.actor_name} 一击落空，被 {entry.target_name} 避开。"
    crit = "暴击" if entry.critical else "命中"
    return f"{prefix} {entry.actor_name} {crit} {entry.target_name}，造成 {format_big_number(entry.damage)} 伤害。"


def _battle_excerpt(battle, limit: int = 6) -> str:
    """从战斗日志中提取最后几条记录。"""
    logs = battle.logs[-limit:] if len(battle.logs) > limit else battle.logs
    lines: list[str] = []
    for entry in logs:
        lines.append(_format_log_entry(entry))
    if len(battle.logs) > limit:
        lines.insert(0, f"*... 省略前 {len(battle.logs) - limit} 条 ...*")
    if battle.reached_round_limit:
        lines.append("*道途漫漫，斗至极限而未分胜负。*")
    return "\n".join(lines) if lines else "战斗瞬间结束。"


# ---------------------------------------------------------------------------
# 词条操作菜单
# ---------------------------------------------------------------------------


def build_pg_affix_menu_embed(
    build: PGBuild,
    pending_affix_ops: int,
) -> discord.Embed:
    """词条操作选择菜单 — 展示当前词条 + 可选操作。"""
    embed = discord.Embed(
        title="📿 词条操作",
        description=f"剩余操作次数：`{pending_affix_ops}`　当前词条：`{len(build.affixes)}` / `{MAX_AFFIX_SLOTS}` 槽",
        color=_PG_COLOR_EVENT,
    )
    if build.affixes:
        affix_lines: list[str] = []
        for i, a in enumerate(build.affixes):
            name, desc = _affix_display(a.affix_id, a.rolls)
            affix_lines.append(f"`{i + 1}.` **{name}**　{desc}")
        embed.add_field(name="当前词条", value="\n".join(affix_lines), inline=False)
    else:
        embed.add_field(name="当前词条", value="暂无词条", inline=False)

    # 操作说明
    ops: list[str] = []
    if len(build.affixes) < MAX_AFFIX_SLOTS:
        ops.append("🆕 **抽取新词条** — 从 3 个候选中选 1 个装入空槽位")
    if len(build.affixes) >= MAX_AFFIX_SLOTS:
        ops.append("🔄 **替换词条** — 先看 3 个候选，再选择替换哪个旧词条")
    if build.affixes:
        ops.append("⬆️ **强化词条** — 选择 1 个已有词条，重 roll 数值取高值")
    if ops:
        embed.add_field(name="可选操作", value="\n".join(ops), inline=False)
    return embed


# ---------------------------------------------------------------------------
# 词条强化选择
# ---------------------------------------------------------------------------


def build_pg_affix_enhance_embed(
    build: PGBuild,
) -> discord.Embed:
    """选择要强化的词条。"""
    embed = discord.Embed(
        title="⬆️ 词条强化",
        description="选择一个已有词条进行强化（重 roll 数值取高值）。",
        color=_PG_COLOR_EVENT,
    )
    for i, a in enumerate(build.affixes):
        name, desc = _affix_display(a.affix_id, a.rolls)
        embed.add_field(
            name=f"词条 {i + 1}：{name}",
            value=desc,
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# 词条替换：3 选 1 后选槽位
# ---------------------------------------------------------------------------


def build_pg_affix_replace_pick_embed(
    choices: list,
    build: PGBuild,
) -> discord.Embed:
    """替换模式的 3 选 1 展示。"""
    embed = discord.Embed(
        title="🔄 词条替换 · 选择新词条",
        description="先选择一个新词条，再决定替换哪个旧词条。",
        color=_PG_COLOR_EVENT,
    )
    for i, affix in enumerate(choices, start=1):
        name, desc = _affix_display(affix.affix_id, affix.rolls)
        embed.add_field(
            name=f"候选 {i}：{name}",
            value=desc,
            inline=False,
        )
    embed.set_footer(text="也可以放弃替换（本次操作机会将消耗）。")
    return embed


def build_pg_affix_replace_slot_embed(
    new_affix_entry,
    build: PGBuild,
) -> discord.Embed:
    """选定新词条后，展示旧词条列表供选择替换槽位。"""
    new_name, new_desc = _affix_display(new_affix_entry.affix_id, new_affix_entry.rolls)
    embed = discord.Embed(
        title="🔄 词条替换 · 选择替换位",
        description=f"即将装入：**{new_name}**　{new_desc}\n\n选择要替换的旧词条：",
        color=_PG_COLOR_EVENT,
    )
    for i, a in enumerate(build.affixes):
        name, desc = _affix_display(a.affix_id, a.rolls)
        embed.add_field(
            name=f"槽位 {i + 1}：{name}",
            value=desc,
            inline=False,
        )
    embed.set_footer(text="也可以放弃替换（本次操作机会将消耗）。")
    return embed


# ---------------------------------------------------------------------------
# 词条选择（新增模式）
# ---------------------------------------------------------------------------


def build_pg_affix_pick_embed(
    choices: list,
    current_affixes: int,
    max_slots: int,
) -> discord.Embed:
    """3 选 1 词条展示。"""
    embed = discord.Embed(
        title="🔮 词条机缘",
        description=f"当前词条：`{current_affixes}` / `{max_slots}` 槽",
        color=_PG_COLOR_EVENT,
    )
    for i, affix in enumerate(choices, start=1):
        name, desc = _affix_display(affix.affix_id, affix.rolls)
        embed.add_field(
            name=f"选项 {i}：{name}",
            value=desc,
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# 器灵操作菜单
# ---------------------------------------------------------------------------


def build_pg_spirit_menu_embed(
    build: PGBuild,
    pending_spirit_ops: int,
) -> discord.Embed:
    """器灵操作选择菜单。"""
    embed = discord.Embed(
        title="🔮 器灵操作",
        description=f"剩余操作次数：`{pending_spirit_ops}`",
        color=_PG_COLOR_EVENT,
    )
    if build.spirit_power is not None:
        sp = build.spirit_power
        name, desc = _spirit_display(sp.power_id, sp.rolls)
        embed.add_field(name="当前器灵", value=f"**{name}**　{desc}", inline=False)
        ops = (
            "🆕 **重新抽取** — 随机获得新器灵，替换当前器灵\n"
            "⬆️ **强化当前** — 重 roll 数值取高值，保留当前神通"
        )
    else:
        embed.add_field(name="当前器灵", value="暂无器灵", inline=False)
        ops = "🆕 **抽取器灵** — 随机获得一个器灵神通"
    embed.add_field(name="可选操作", value=ops, inline=False)
    return embed


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


def build_pg_event_embed(
    event: PGEvent,
    *,
    result: PGEventResult | None = None,
) -> discord.Embed:
    """问号事件界面。"""
    if result is not None:
        embed = discord.Embed(
            title=f"❓ {event.name}",
            description=result.message,
            color=_PG_COLOR_SUCCESS if result.success else _PG_COLOR_FAILURE,
        )
        if result.score_gained > 0:
            embed.set_footer(text=f"积分 +{result.score_gained}")
        return embed

    embed = discord.Embed(
        title=f"❓ {event.name}",
        description=event.description,
        color=_PG_COLOR_EVENT,
    )
    if event.choices:
        choice_lines: list[str] = []
        for c in event.choices:
            risk_mark = " ⚠️" if c.risk else ""
            choice_lines.append(f"**{c.label}**{risk_mark}\n└ {c.description}")
        embed.add_field(name="选择", value="\n\n".join(choice_lines), inline=False)
    elif event.auto_apply:
        embed.set_footer(text="此事件自动生效。")
    return embed


# ---------------------------------------------------------------------------
# 构筑查看
# ---------------------------------------------------------------------------


def build_pg_build_embed(
    build: PGBuild,
    pending_affix_ops: int,
    pending_spirit_ops: int,
) -> discord.Embed:
    """当前构筑面板。"""
    embed = discord.Embed(
        title="📋 证道构筑",
        color=_PG_COLOR,
    )

    # 三维
    embed.add_field(
        name="⚡ 面板属性",
        value=(
            f"攻击：`{format_big_number(build.effective_atk())}` ({build.atk_pct_bonus:+d}%)\n"
            f"防御：`{format_big_number(build.effective_def())}` ({build.def_pct_bonus:+d}%)\n"
            f"身法：`{format_big_number(build.effective_agi())}` ({build.agi_pct_bonus:+d}%)"
        ),
        inline=True,
    )

    # 操作次数
    embed.add_field(
        name="🔧 剩余操作",
        value=(
            f"词条操作：`{pending_affix_ops}`\n"
            f"器灵操作：`{pending_spirit_ops}`"
        ),
        inline=True,
    )

    # 词条列表
    if build.affixes:
        affix_lines: list[str] = []
        for a in build.affixes:
            name, desc = _affix_display(a.affix_id, a.rolls)
            affix_lines.append(f"• **{name}**　{desc}")
        embed.add_field(name=f"📿 词条 ({len(build.affixes)}/5)", value="\n".join(affix_lines), inline=False)
    else:
        embed.add_field(name="📿 词条 (0/5)", value="暂无词条", inline=False)

    # 器灵
    if build.spirit_power is not None:
        sp = build.spirit_power
        name, desc = _spirit_display(sp.power_id, sp.rolls)
        embed.add_field(name="🔮 器灵神通", value=f"**{name}**　{desc}", inline=False)
    else:
        embed.add_field(name="🔮 器灵神通", value="暂无器灵", inline=False)

    return embed


# ---------------------------------------------------------------------------
# 结算
# ---------------------------------------------------------------------------


def build_pg_settlement_embed(
    settlement: PGSettlement,
    build: PGBuild,
) -> discord.Embed:
    """运行结算界面。"""
    if settlement.victory:
        embed = discord.Embed(
            title="🏛️ 证道成功",
            description="天道认可你的实力，证道之行圆满落幕。",
            color=_PG_COLOR_SETTLEMENT,
        )
    else:
        embed = discord.Embed(
            title="🏛️ 证道未竟",
            description="此行虽未功成，积累的经验亦非无用。",
            color=_PG_COLOR_FAILURE,
        )

    # 积分
    embed.add_field(
        name="📊 积分结算",
        value=f"本次积分：`{settlement.total_score}`",
        inline=True,
    )

    # 道痕
    if settlement.dao_traces_gained > 0:
        embed.add_field(name="✨ 道痕", value=f"+{settlement.dao_traces_gained}", inline=True)

    # BOSS
    if settlement.boss_type:
        boss_name = PG_BOSS_DISPLAY_NAMES.get(settlement.boss_type, settlement.boss_type)
        status = "已击败 ✅" if settlement.boss_killed else "未击败 ❌"
        embed.add_field(name="👹 BOSS", value=f"{boss_name}　{status}", inline=True)

    # 荣誉
    if settlement.honor_gained:
        embed.add_field(name="🏅 获得荣誉", value=f"**{settlement.honor_gained}**", inline=False)

    # 最终构筑
    affix_count = len(build.affixes)
    spirit = build.spirit_power
    embed.add_field(
        name="📋 最终构筑",
        value=(
            f"攻击：`{format_big_number(build.effective_atk())}`　"
            f"防御：`{format_big_number(build.effective_def())}`　"
            f"身法：`{format_big_number(build.effective_agi())}`\n"
            f"词条 ×{affix_count}　器灵：{'✅ ' + _spirit_display(spirit.power_id, spirit.rolls)[0] if spirit else '❌'}"
        ),
        inline=False,
    )

    return embed
