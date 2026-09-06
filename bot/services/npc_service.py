"""NPC 经济填充剂 Service。

设计理念：NPC = "随机存档的玩家"。
- 每天 0 点全清重生；3/6/9/12/15/18/21 点只补悬赏被打空的魔道名额
- 不参与任何主动行为：不闭关/不游历/不打塔/不入宗门/不开擂
- 仅作为「可被打的目标」存在：悬赏列表 / 劫掠列表 / 战斗管线
- 境界固定四档；器魂跟全服真人第二高走同档倍率，悬赏跟全服真人最高恶名走同档倍率
- 完全融入真人 Character 表，靠 is_npc 字段区分
"""

from __future__ import annotations

import random
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.npc_names import generate_xianxia_name
from bot.data.realms import get_stage
from bot.data.spirits import SPIRIT_TIER_ORDER
from bot.models import Artifact, Character, LadderRecord, Player
from bot.services.faction_service import INFAMY_BY_REALM
from bot.utils.time_utils import ensure_shanghai, now_shanghai, today_shanghai

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

    REFRESH_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
    STAGE_KEYS = ("early", "mid", "late", "perfect")
    REALM_SPAWN_BANDS = (
        (0.50, ("lianqi", "zhuji", "jiedan"), 0.01, 0.05),
        (0.30, ("yuanying", "huashen", "lianxu"), 0.05, 0.10),
        (0.15, ("heti", "dacheng", "dujie"), 0.10, 0.15),
        (0.05, ("weixian",), 0.15, 0.20),
    )

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

    async def ensure_daily_pool(self, session: AsyncSession, *, now=None) -> int:
        """0 点全换；其余 3 小时整点只补悬赏被打空的魔道名额。"""
        current = ensure_shanghai(now or now_shanghai())
        today = current.date()
        npcs = await self._load_npcs(session)

        if not any(n.npc_spawned_on == today for n in npcs):
            return await self._rebuild_pool(session, npcs, today)
        if current.hour not in self.REFRESH_HOURS or current.hour == 0:
            return 0
        emptied = [n for n in npcs if n.faction == "demonic" and (n.bounty_soul or 0) <= 0]
        if not emptied:
            return 0
        remaining = len(npcs) - len(emptied)
        await self._delete_npcs(session, emptied)
        room = max(0, self.DAILY_POOL_SIZE - remaining)
        return await self._spawn_many(session, today, min(len(emptied), room), force_demonic=True)

    async def _load_npcs(self, session: AsyncSession) -> list[Character]:
        return list(
            (
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
        )

    async def _load_real_chars(self, session: AsyncSession) -> list[Character]:
        return list(
            (
                await session.scalars(
                    select(Character)
                    .where(Character.is_npc.is_(False))
                    .options(
                        selectinload(Character.player),
                        selectinload(Character.artifact),
                    )
                )
            ).all()
        )

    async def _delete_npcs(self, session: AsyncSession, npcs: list[Character]) -> None:
        for npc in npcs:
            if npc.player is not None:
                await session.delete(npc.player)
            else:
                await session.delete(npc)
        await session.flush()

    async def _rebuild_pool(self, session: AsyncSession, npcs: list[Character], today) -> int:
        await self._delete_npcs(session, npcs)
        return await self._spawn_many(session, today, self.DAILY_POOL_SIZE)

    async def _spawn_many(self, session: AsyncSession, today, count: int, *, force_demonic: bool = False) -> int:
        if count <= 0:
            return 0
        real_chars = await self._load_real_chars(session)
        if not real_chars:
            return 0
        caps = self._compute_caps(real_chars)
        existing = await self._load_npcs(session)
        start_index = len(existing)
        spawned = 0
        for offset in range(count):
            await self._spawn_one(
                session,
                caps,
                real_chars,
                today,
                start_index + offset,
                force_demonic=force_demonic,
            )
            spawned += 1
        await session.flush()
        return spawned

    # ------------------------------------------------------------------
    # caps 计算（防捡漏）
    # ------------------------------------------------------------------

    def _compute_caps(self, real_chars: list[Character]) -> dict[str, int]:
        """从真人样本计算各项资源上限。

        灵石/器魂去极值（第二高）；悬赏上限取全服真人最高恶名。
        """
        return {
            "max_reinforce": max(
                (c.artifact.reinforce_level for c in real_chars if c.artifact),
                default=0,
            ),
            "max_lingshi": self._dampened_cap([c.lingshi for c in real_chars]),
            "max_bounty": max((c.infamy or 0 for c in real_chars), default=0),
            "max_virtue": max((c.virtue for c in real_chars), default=0),
            "max_infamy": max((c.infamy for c in real_chars), default=0),
            "max_soul_shards": self._dampened_cap(
                [c.artifact.soul_shards for c in real_chars if c.artifact],
            ),
            "max_travel_atk": max((c.travel_atk_pct for c in real_chars), default=0),
            "max_travel_def": max((c.travel_def_pct for c in real_chars), default=0),
            "max_travel_agi": max((c.travel_agi_pct for c in real_chars), default=0),
            "max_spirit_tier_idx": self._max_spirit_tier_idx(real_chars),
        }

    @staticmethod
    def _dampened_cap(values: list[int], drop_top: int = 1) -> int:
        """去极端值上限：排序后跳过最高 N 个值，再取 max。

        防止单个超高值（如一次性讨伐获得巨额器魂）拉高全体 NPC 上限，
        同时保留多数玩家的真实水平作为 NPC 生成参考。样本不足时保守回落。
        """
        if not values:
            return 0
        sorted_vals = sorted(values, reverse=True)
        # 至少保留一个值
        keep_from = min(drop_top, len(sorted_vals) - 1)
        return sorted_vals[keep_from]

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

    def _pick_realm_band(self):
        roll = self.rng.random()
        cumulative = 0.0
        for weight, realm_keys, lo, hi in self.REALM_SPAWN_BANDS:
            cumulative += weight
            if roll < cumulative:
                return realm_keys, lo, hi
        realm_keys, lo, hi = self.REALM_SPAWN_BANDS[-1][1:]
        return realm_keys, lo, hi

    def _roll_realm_stage(self):
        realm_keys, lo, hi = self._pick_realm_band()
        realm_key = self.rng.choice(realm_keys)
        stage_key = self.rng.choice(self.STAGE_KEYS)
        return get_stage(realm_key, stage_key), lo, hi

    def _band_amount(self, cap: int, lo: float, hi: float, *, floor: int = 0) -> int:
        if cap <= 0:
            return floor
        amount = max(1, int(cap * self.rng.uniform(lo, hi)))
        return min(amount, cap)

    # ------------------------------------------------------------------
    # 单个 NPC 生成
    # ------------------------------------------------------------------

    async def _spawn_one(
        self,
        session: AsyncSession,
        caps: dict[str, int],
        real_chars: list[Character],
        today,
        index: int,
        *,
        force_demonic: bool = False,
    ) -> None:
        """生成单个 NPC：固定境界档 + 同档悬赏/器魂倍率。"""
        stage, band_lo, band_hi = self._roll_realm_stage()
        realm_key = stage.realm_key
        stage_key = stage.stage_key

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
        is_demonic = force_demonic or self.rng.random() < self.DEMONIC_RATIO
        if is_demonic:
            faction = "demonic"
            bounty = self._roll_bounty(caps["max_bounty"], realm_key=realm_key, lo=band_lo, hi=band_hi)
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
        # ---- 6b. 器魂：上限=真人器魂第二高，倍率跟大境界档走 ----
        soul_shards = self._roll_soul_shards(caps["max_soul_shards"], lo=band_lo, hi=band_hi)

        # ---- 7. 法宝强化拉满该境界 cap；三维取同境真人最高/最低均值 ±30% ----
        reinforce_level = stage.reinforce_cap
        atk_b, def_b, agi_b = self._roll_artifact_bonuses(stage, real_chars, fate.key)

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
            historical_max_infamy=infamy,
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
            soul_shards=soul_shards,
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
        """灵石三段分布：30% 穷 / 50% 中 / 20% 富，硬上限 ≤ 全服最高。

        2026-05-13 砍乘数：原 0.05-0.30 / 0.30-1.00 / 1.00-1.50（最高 150%）
                          → 0.05-0.20 / 0.20-0.60 / 0.60-1.00（最高 100%）
        理由：配合恶名清空机制，控制 NPC 经济注入。
        """
        if max_lingshi <= 0:
            return 0
        bucket = self.rng.random()
        if bucket < 0.30:
            mult = self.rng.uniform(0.05, 0.20)
        elif bucket < 0.80:
            mult = self.rng.uniform(0.20, 0.60)
        else:
            mult = self.rng.uniform(0.60, 1.00)
        amount = int(max_lingshi * mult)
        return min(amount, max_lingshi)

    def _roll_bounty(self, max_bounty: int, *, realm_key: str, lo: float, hi: float) -> int:
        """上限=全服真人最高恶名；全服恶名为 0 时按该境界固定恶名保底。"""
        floor = INFAMY_BY_REALM.get(realm_key, 50)
        return self._band_amount(max_bounty, lo, hi, floor=floor)

    def _roll_soul_shards(self, max_soul_shards: int, *, lo: float, hi: float) -> int:
        """上限=全服真人器魂第二高，倍率跟 NPC 大境界档走。"""
        return self._band_amount(max_soul_shards, lo, hi)

    # ------------------------------------------------------------------
    # 法宝三维成长模拟
    # ------------------------------------------------------------------

    def _roll_artifact_bonuses(self, stage, real_chars: list[Character], fate_key: str) -> tuple[int, int, int]:
        peers = [c for c in real_chars if c.realm_key == stage.realm_key]
        if not peers:
            return 0, 0, 0
        stats = [self.character_service.calculate_total_stats(c) for c in peers]
        return (
            self._bonus_for_target(min(s.atk for s in stats), max(s.atk for s in stats), stage.base_atk, fate_key, "atk"),
            self._bonus_for_target(min(s.defense for s in stats), max(s.defense for s in stats), stage.base_def, fate_key, "def"),
            self._bonus_for_target(min(s.agility for s in stats), max(s.agility for s in stats), stage.base_agi, fate_key, "agi"),
        )

    def _bonus_for_target(self, low: int, high: int, base: int, fate_key: str, stat: str) -> int:
        target = max(1, int((low + high) / 2 * self.rng.uniform(0.70, 1.30)))
        mult = self.fate_service.stat_multiplier(fate_key, stat)
        raw = int(target / mult) if mult else target
        return max(0, raw - base)
