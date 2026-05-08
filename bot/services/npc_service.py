"""NPC 经济填充剂 Service。

设计理念：NPC = "随机存档的玩家"。
- 每天 0 点全清重生（懒结算 + 启动兜底触发 ensure_daily_pool）
- 不参与任何主动行为：不闭关/不游历/不打塔/不入宗门/不开擂
- 仅作为「可被打的目标」存在：悬赏列表 / 劫掠列表 / 战斗管线
- 所有资源 ≤ 全服真人最高（防捡漏）
- 完全融入真人 Character 表，靠 is_npc 字段区分
"""

from __future__ import annotations

import random
import uuid
from collections import Counter
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.npc_names import generate_xianxia_name
from bot.data.realms import get_stage
from bot.data.spirits import SPIRIT_TIER_ORDER
from bot.models import Artifact, Character, LadderRecord, Player
from bot.utils.time_utils import now_shanghai, today_shanghai

if TYPE_CHECKING:
    from bot.services.artifact_service import ArtifactService
    from bot.services.character_service import CharacterService
    from bot.services.fate_service import FateService
    from bot.services.spirit_service import SpiritService


class NpcService:
    """NPC 池管理：每日刷新 + 8 维随机 + caps 防捡漏。"""

    DAILY_POOL_SIZE = 30
    """每日 NPC 池规模（正魔比例由 faction_demonic_ratio 控制）"""

    DEMONIC_RATIO = 0.5
    """魔道 NPC 比例（带悬赏，可被讨伐）"""

    SPIRIT_REROLL_LIMIT = 5
    """器灵品阶超 cap 时的重 roll 上限，防死循环"""

    def __init__(
        self,
        rng: random.Random,
        character_service: "CharacterService",
        fate_service: "FateService",
        artifact_service: "ArtifactService",
        spirit_service: "SpiritService",
    ) -> None:
        self.rng = rng
        self.character_service = character_service
        self.fate_service = fate_service
        self.artifact_service = artifact_service
        self.spirit_service = spirit_service

    # ------------------------------------------------------------------
    # 主入口：每日刷新（幂等）
    # ------------------------------------------------------------------

    async def ensure_daily_pool(self, session: AsyncSession) -> int:
        """幂等：当日已有 NPC 则跳过；否则删旧 NPC + 生成新池。

        返回值：本次新增 NPC 数（0 = 当日已生成或无真人样本）。
        """
        today = today_shanghai()

        npcs = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(True))
                .options(
                    selectinload(Character.player),
                    selectinload(Character.artifact),
                    selectinload(Character.ladder_record),
                )
            )
        ).all()

        already_today = [n for n in npcs if n.npc_spawned_on == today]
        if already_today:
            return 0  # 当日已刷新

        # 删除旧 NPC（cascade 自动清理 character / artifact / ladder_record）
        for npc in npcs:
            if npc.player is not None:
                await session.delete(npc.player)
            else:
                await session.delete(npc)
        await session.flush()

        # 取真人样本（NPC 不计入 caps 计算）
        real_chars = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(False))
                .options(
                    selectinload(Character.player),
                    selectinload(Character.artifact),
                )
            )
        ).all()
        if not real_chars:
            return 0  # 无真人样本，不生成（避免冷启动空 caps 兜底复杂度）

        caps = self._compute_caps(real_chars)
        realm_dist = self._realm_distribution(real_chars)
        if not realm_dist:
            return 0

        spawned = 0
        for index in range(self.DAILY_POOL_SIZE):
            await self._spawn_one(session, caps, realm_dist, today, index)
            spawned += 1
        await session.flush()
        return spawned

    # ------------------------------------------------------------------
    # caps 计算（防捡漏）
    # ------------------------------------------------------------------

    def _compute_caps(self, real_chars: list[Character]) -> dict[str, int]:
        """从真人样本计算各项资源上限。"""
        return {
            "max_reinforce": max(
                (c.artifact.reinforce_level for c in real_chars if c.artifact),
                default=0,
            ),
            "max_lingshi": max((c.lingshi for c in real_chars), default=0),
            "max_bounty": max((c.bounty_soul for c in real_chars), default=0),
            "max_virtue": max((c.virtue for c in real_chars), default=0),
            "max_infamy": max((c.infamy for c in real_chars), default=0),
            "max_travel_atk": max((c.travel_atk_pct for c in real_chars), default=0),
            "max_travel_def": max((c.travel_def_pct for c in real_chars), default=0),
            "max_travel_agi": max((c.travel_agi_pct for c in real_chars), default=0),
            "max_spirit_tier_idx": self._max_spirit_tier_idx(real_chars),
        }

    def _max_spirit_tier_idx(self, chars: list[Character]) -> int:
        """全服真人器灵的最高品阶索引（-1 表示尚无器灵）。"""
        best = -1
        for c in chars:
            if not c.artifact:
                continue
            sp = self.spirit_service.get_current_spirit(c.artifact)
            if sp is None:
                continue
            try:
                idx = SPIRIT_TIER_ORDER.index(sp.tier)
            except ValueError:
                continue
            if idx > best:
                best = idx
        return best

    def _realm_distribution(
        self, real_chars: list[Character]
    ) -> Counter[tuple[str, str]]:
        """真人境界分布（用于按权重 roll NPC 境界，实现 E3 镜像）。"""
        return Counter((c.realm_key, c.stage_key) for c in real_chars)

    # ------------------------------------------------------------------
    # 单个 NPC 生成
    # ------------------------------------------------------------------

    async def _spawn_one(
        self,
        session: AsyncSession,
        caps: dict[str, int],
        realm_dist: Counter[tuple[str, str]],
        today,
        index: int,
    ) -> None:
        """生成单个 NPC：境界镜像 + caps 限制 + 完整 Character/Artifact/LadderRecord。"""
        # ---- 1. 境界镜像（按真人分布权重 roll） ----
        items = list(realm_dist.keys())
        weights = list(realm_dist.values())
        realm_key, stage_key = self.rng.choices(items, weights=weights, k=1)[0]
        stage = get_stage(realm_key, stage_key)

        # ---- 2. 修为：该 stage 内 0 ~ 90% ----
        cultivation = self.rng.randint(0, max(0, int(stage.cultivation_max * 0.9)))

        # ---- 3. 道号 + 虚拟 Player ----
        name = generate_xianxia_name(self.rng)
        fake_id = f"npc:{today.isoformat()}:{index:02d}:{uuid.uuid4().hex[:8]}"
        player = Player(discord_user_id=fake_id, display_name=name)

        # ---- 4. 命格 + 福缘 ----
        fate = self.fate_service.roll_fate()
        luck = self.fate_service.random_initial_luck()

        # ---- 5. 阵营 + 悬赏/罪业/功德 ----
        is_demonic = (
            self.rng.random() < self.DEMONIC_RATIO and caps["max_bounty"] > 0
        )
        if is_demonic:
            faction = "demonic"
            bounty = self.rng.randint(1, caps["max_bounty"])
            infamy = (
                self.rng.randint(100, max(101, caps["max_infamy"]))
                if caps["max_infamy"] > 0
                else self.rng.randint(100, 5000)
            )
            virtue = 0
        else:
            faction = "neutral"
            bounty = 0
            infamy = 0
            virtue = (
                self.rng.randint(0, caps["max_virtue"])
                if caps["max_virtue"] > 0
                else 0
            )

        # ---- 6. 灵石（三段分布 × caps 限制） ----
        lingshi = self._roll_lingshi(caps["max_lingshi"])

        # ---- 7. 法宝强化等级 + 三维成长 ----
        reinforce_cap = min(caps["max_reinforce"], stage.reinforce_cap)
        reinforce_level = (
            self.rng.randint(0, reinforce_cap) if reinforce_cap > 0 else 0
        )
        atk_b, def_b, agi_b = self._roll_artifact_growth(stage, reinforce_level)

        # ---- 8. 论道排名（NPC 不上榜，给占位高 rank） ----
        placeholder_rank = 9000 + index

        # ---- 9. 创建 Character ----
        now_dt = now_shanghai()
        npc_char = Character(
            player=player,
            realm_key=realm_key,
            realm_index=stage.realm_index,
            stage_key=stage_key,
            stage_index=stage.stage_index,
            cultivation=cultivation,
            highest_floor=0,
            historical_highest_floor=0,
            current_qi=6,
            qi_max=6,
            is_retreating=False,
            retreat_mode="cultivation",
            is_traveling=False,
            last_idle_at=now_dt,
            travel_started_at=now_dt,
            travel_duration_minutes=0,
            travel_selected_duration_minutes=120,
            travel_atk_pct=0,
            travel_def_pct=0,
            travel_agi_pct=0,
            last_qi_recovered_at=now_dt,
            sect_id=None,
            sect_joined_at=None,
            sect_last_left_at=None,
            sect_contribution_total=0,
            sect_contribution_weekly=0,
            sect_contribution_daily=0,
            sect_last_contribution_on=None,
            sect_last_settlement_on=None,
            sect_last_settlement_summary="",
            sect_task_refresh_on=None,
            sect_task_state_json="",
            lingshi=lingshi,
            fate_key=fate.key,
            honor_tags_json="[]",
            faction=faction,
            virtue=virtue,
            infamy=infamy,
            luck=luck,
            bounty_soul=bounty,
            current_ladder_rank=placeholder_rank,
            best_ladder_rank=placeholder_rank,
            last_highlight_text="散修一名，江湖无名。",
            is_npc=True,
            npc_spawned_on=today,
        )

        # ---- 10. 创建 Artifact ----
        npc_char.artifact = Artifact(
            name=self.artifact_service.create_initial_name(),
            artifact_rename_used=False,
            reinforce_level=reinforce_level,
            atk_bonus=atk_b,
            def_bonus=def_b,
            agi_bonus=agi_b,
            soul_shards=0,
            affix_slots_json="[]",
            affix_pending_json="[]",
            spirit_name=None,
            spirit_rename_used=False,
            spirit_json="",
            spirit_pending_json="",
            spirit_refining_until=None,
            spirit_refining_mode=None,
        )

        # ---- 11. LadderRecord ----
        npc_char.ladder_record = LadderRecord(
            rank=placeholder_rank, wins=0, losses=0, streak=0
        )

        # ---- 12. 解锁词条（按 reinforce_level 自动 roll 槽位） ----
        self.artifact_service.ensure_affix_slots(npc_char.artifact)

        # ---- 13. 解锁器灵（≥30 级 + 真人已出现器灵时） ----
        if (
            self.spirit_service.is_unlocked(npc_char.artifact)
            and caps["max_spirit_tier_idx"] >= 0
        ):
            spirit = self.spirit_service._roll_spirit(npc_char.artifact)
            # 品阶超 cap 时重 roll，最多 SPIRIT_REROLL_LIMIT 次
            for _ in range(self.SPIRIT_REROLL_LIMIT):
                try:
                    actual_idx = SPIRIT_TIER_ORDER.index(spirit.tier)
                except ValueError:
                    break
                if actual_idx <= caps["max_spirit_tier_idx"]:
                    break
                spirit = self.spirit_service._roll_spirit(npc_char.artifact)
            self.spirit_service._store_spirit(
                npc_char.artifact, "spirit_json", spirit
            )
            npc_char.artifact.spirit_name = self.spirit_service.create_initial_name()

        # ---- 14. 刷新战力 ----
        self.character_service.refresh_combat_power(npc_char)

        session.add(player)

    # ------------------------------------------------------------------
    # 灵石三段分布
    # ------------------------------------------------------------------

    def _roll_lingshi(self, max_lingshi: int) -> int:
        """灵石三段分布：30% 穷 / 50% 中 / 20% 富，硬上限 ≤ 全服最高。"""
        if max_lingshi <= 0:
            return 0
        bucket = self.rng.random()
        if bucket < 0.30:
            mult = self.rng.uniform(0.05, 0.30)
        elif bucket < 0.80:
            mult = self.rng.uniform(0.30, 1.00)
        else:
            mult = self.rng.uniform(1.00, 1.50)
        amount = int(max_lingshi * mult)
        return min(amount, max_lingshi)

    # ------------------------------------------------------------------
    # 法宝三维成长模拟
    # ------------------------------------------------------------------

    def _roll_artifact_growth(
        self, stage, reinforce_level: int
    ) -> tuple[int, int, int]:
        """模拟 reinforce_level 次强化后的 atk/def/agi 加成（每次随机投点到三维之一）。"""
        if reinforce_level <= 0:
            return 0, 0, 0
        growth_total = self.artifact_service._growth_total(stage)
        total_points = growth_total * reinforce_level
        atk_b = def_b = agi_b = 0
        for _ in range(total_points):
            choice = self.rng.randint(0, 2)
            if choice == 0:
                atk_b += 1
            elif choice == 1:
                def_b += 1
            else:
                agi_b += 1
        return atk_b, def_b, agi_b
