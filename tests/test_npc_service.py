"""NpcService 测试套件。

覆盖：
- ensure_daily_pool 主流程（无真人 / 单真人 30 NPC / 幂等 / 跨日替换）
- caps 防捡漏（lingshi / reinforce / bounty 不超真人最高）
- 境界镜像（NPC 境界来自真人分布）
- 系统过滤（榜单 / 论道 不出现 NPC）
- 纯函数（_compute_caps / _realm_distribution / _roll_lingshi / _roll_artifact_growth）
- cascade 删除（删 player 自动清 character/artifact/ladder_record）
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.data.realms import get_stage
from bot.models import Artifact, Character, LadderRecord, Player
from bot.utils.time_utils import today_shanghai


# ---------------------------------------------------------------------------
# 辅助：创建一个真人样本（不依赖游戏内 service 流程，直接造数据）
# ---------------------------------------------------------------------------


async def _create_real_player(
    session,
    services,
    *,
    discord_id: int,
    name: str,
    realm_key: str = "lianqi",
    stage_key: str = "early",
    lingshi: int = 0,
    bounty: int = 0,
    virtue: int = 0,
    infamy: int = 0,
    reinforce_level: int = 0,
    faction: str = "neutral",
) -> Character:
    """通过 character_service 创建真人 Character 后修补到目标境界 / 资源。"""
    result = await services.character.get_or_create_character(session, discord_id, name)
    char = result.character
    stage = get_stage(realm_key, stage_key)
    char.realm_key = realm_key
    char.realm_index = stage.realm_index
    char.stage_key = stage_key
    char.stage_index = stage.stage_index
    char.lingshi = lingshi
    char.bounty_soul = bounty
    char.virtue = virtue
    char.infamy = infamy
    char.faction = faction
    char.artifact.reinforce_level = reinforce_level
    return char


# ---------------------------------------------------------------------------
# ensure_daily_pool 主流程
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_daily_pool_returns_zero_when_no_real_players(
    session_factory, services
) -> None:
    """无真人样本时直接返回 0，不生成 NPC。"""
    async with session_factory() as session:
        spawned = await services.npc.ensure_daily_pool(session)
        assert spawned == 0

        npc_count = (
            await session.scalar(
                select(Character).where(Character.is_npc.is_(True))
            )
        )
        assert npc_count is None


@pytest.mark.asyncio
async def test_ensure_daily_pool_spawns_full_pool_with_real_players(
    session_factory, services
) -> None:
    """有真人样本时生成 30 个 NPC，且 is_npc / npc_spawned_on 字段正确。"""
    async with session_factory() as session:
        await _create_real_player(session, services, discord_id=9001, name="真人甲")
        await session.flush()

        spawned = await services.npc.ensure_daily_pool(session)
        assert spawned == services.npc.DAILY_POOL_SIZE

        npcs = (
            await session.scalars(
                select(Character).where(Character.is_npc.is_(True))
            )
        ).all()
        assert len(npcs) == services.npc.DAILY_POOL_SIZE
        today = today_shanghai()
        for npc in npcs:
            assert npc.is_npc is True
            assert npc.npc_spawned_on == today


@pytest.mark.asyncio
async def test_ensure_daily_pool_is_idempotent_within_same_day(
    session_factory, services
) -> None:
    """当日重复调用幂等：第二次返回 0，NPC 数量不变。"""
    async with session_factory() as session:
        await _create_real_player(session, services, discord_id=9001, name="真人甲")
        await session.flush()

        first = await services.npc.ensure_daily_pool(session)
        second = await services.npc.ensure_daily_pool(session)

        assert first == services.npc.DAILY_POOL_SIZE
        assert second == 0

        npcs = (
            await session.scalars(
                select(Character).where(Character.is_npc.is_(True))
            )
        ).all()
        assert len(npcs) == services.npc.DAILY_POOL_SIZE


@pytest.mark.asyncio
async def test_ensure_daily_pool_replaces_yesterday_pool(
    session_factory, services
) -> None:
    """跨日：旧 NPC 全部清掉，生成新池。

    用 discord_user_id 区分新旧（NPC 虚拟 ID 含日期前缀），
    避免 SQLite 删除后主键重用造成 id 冲突。
    """
    from sqlalchemy.orm import selectinload

    async with session_factory() as session:
        await _create_real_player(session, services, discord_id=9001, name="真人甲")
        await session.flush()

        # 第一次生成
        await services.npc.ensure_daily_pool(session)
        await session.flush()

        # 把所有 NPC 的 spawned_on 改为昨天
        yesterday = today_shanghai() - timedelta(days=1)
        npcs_old = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(True))
                .options(selectinload(Character.player))
            )
        ).all()
        old_user_ids = {n.player.discord_user_id for n in npcs_old}
        for npc in npcs_old:
            npc.npc_spawned_on = yesterday
        await session.flush()

        # 第二次调用应当替换旧池
        spawned = await services.npc.ensure_daily_pool(session)
        assert spawned == services.npc.DAILY_POOL_SIZE

        npcs_new = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(True))
                .options(selectinload(Character.player))
            )
        ).all()
        assert len(npcs_new) == services.npc.DAILY_POOL_SIZE
        new_user_ids = {n.player.discord_user_id for n in npcs_new}
        assert new_user_ids.isdisjoint(old_user_ids), "新 NPC 不应复用旧 discord_user_id"
        # 旧 NPC 应已被 cascade 删除（通过 player_id 检查）
        assert all(yesterday.isoformat() not in uid for uid in new_user_ids)


# ---------------------------------------------------------------------------
# caps 防捡漏
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_resources_never_exceed_real_player_caps(
    session_factory, services
) -> None:
    """所有 NPC 的 lingshi / reinforce_level / bounty 都 ≤ 真人最高。"""
    async with session_factory() as session:
        await _create_real_player(
            session,
            services,
            discord_id=9001,
            name="顶配真人",
            lingshi=10000,
            bounty=500,
            reinforce_level=20,
            virtue=300,
        )
        await session.flush()

        await services.npc.ensure_daily_pool(session)
        await session.flush()

        npcs = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(True))
            )
        ).all()
        # 重新加载 artifact 关联
        for npc in npcs:
            await session.refresh(npc, ["artifact"])

        assert all(n.lingshi <= 10000 for n in npcs)
        assert all(n.bounty_soul <= 500 for n in npcs)
        assert all(n.artifact.reinforce_level <= 20 for n in npcs)


@pytest.mark.asyncio
async def test_demonic_npcs_have_bounty_neutral_npcs_have_no_bounty(
    session_factory, services
) -> None:
    """魔道 NPC 必带 bounty；中立 NPC 必无 bounty。"""
    async with session_factory() as session:
        await _create_real_player(
            session,
            services,
            discord_id=9001,
            name="参考真人",
            bounty=200,
            virtue=100,
            infamy=300,
        )
        await session.flush()

        await services.npc.ensure_daily_pool(session)
        await session.flush()

        npcs = (
            await session.scalars(
                select(Character).where(Character.is_npc.is_(True))
            )
        ).all()

        for npc in npcs:
            if npc.faction == "demonic":
                assert npc.bounty_soul > 0, "魔道 NPC 必须带 bounty"
            else:
                assert npc.bounty_soul == 0, "非魔道 NPC 不应有 bounty"


# ---------------------------------------------------------------------------
# 系统过滤：NPC 不出现在榜单 / 论道挑战目标
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npcs_excluded_from_leaderboards(
    session_factory, services
) -> None:
    """通过 ranking_service 取榜单不应出现 NPC（按 player_name 比对）。"""
    from sqlalchemy.orm import selectinload

    async with session_factory() as session:
        real = await _create_real_player(
            session, services, discord_id=9001, name="真人甲"
        )
        await session.flush()

        await services.npc.ensure_daily_pool(session)
        await session.flush()

        # 收集所有 NPC 的显示名
        npcs = (
            await session.scalars(
                select(Character)
                .where(Character.is_npc.is_(True))
                .options(selectinload(Character.player))
            )
        ).all()
        npc_names = {n.player.display_name for n in npcs}
        assert npc_names, "测试前置条件：应有 NPC"

        result = await services.ranking.build_leaderboard(
            session, "power", viewer=real, limit=50
        )
        for entry in result.entries:
            assert entry.player_name not in npc_names, (
                f"榜单不应出现 NPC：{entry.player_name}"
            )


@pytest.mark.asyncio
async def test_npcs_excluded_from_ladder_challenge_targets(
    session_factory, services
) -> None:
    """论道挑战目标列表不应出现 NPC（NPC 用 9000+ 占位 rank）。"""
    async with session_factory() as session:
        real = await _create_real_player(
            session, services, discord_id=9001, name="真人甲"
        )
        # 给真人一个论道排名（不为 1，否则 early return）
        real.current_ladder_rank = 100
        await session.flush()

        await services.npc.ensure_daily_pool(session)
        await session.flush()

        targets = await services.ladder.get_challenge_targets(session, real)
        # 不应包含任何 rank >= 9000 的 NPC 占位
        for t in targets:
            assert t.rank < 9000, "论道目标不应出现 NPC 占位 rank"


# ---------------------------------------------------------------------------
# 纯函数单测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_caps_with_empty_list_returns_zero_caps(services) -> None:
    """边界：空列表（理论上不会发生，但防御性测试）。"""
    caps = services.npc._compute_caps([])
    assert caps["max_lingshi"] == 0
    assert caps["max_reinforce"] == 0
    assert caps["max_bounty"] == 0
    assert caps["max_spirit_tier_idx"] == -1


@pytest.mark.asyncio
async def test_compute_caps_dampened_for_economy_resources(
    session_factory, services
) -> None:
    """经济资源（灵石/悬赏/器魂）应使用去极值上限，跳过最高值防止离群污染。"""
    async with session_factory() as session:
        a = await _create_real_player(
            session, services, discord_id=9001, name="A", lingshi=500, bounty=10
        )
        b = await _create_real_player(
            session, services, discord_id=9002, name="B", lingshi=2000, bounty=5
        )
        c = await _create_real_player(
            session, services, discord_id=9003, name="C", lingshi=100, bounty=99
        )
        await session.flush()

        caps = services.npc._compute_caps([a, b, c])
        # 去掉最高值 2000 后，max 为 500
        assert caps["max_lingshi"] == 500
        # 去掉最高值 99 后，max 为 10
        assert caps["max_bounty"] == 10


def test_realm_distribution_counts_pairs() -> None:
    """_realm_distribution 应按 (realm_key, stage_key) 计数。"""
    from bot.services.npc_service import NpcService

    rng = random.Random(42)
    svc = NpcService.__new__(NpcService)
    svc.rng = rng

    # 用 SimpleNamespace 模拟，因为只读两个属性
    from types import SimpleNamespace

    chars = [
        SimpleNamespace(realm_key="lianqi", stage_key="early"),
        SimpleNamespace(realm_key="lianqi", stage_key="early"),
        SimpleNamespace(realm_key="lianqi", stage_key="mid"),
        SimpleNamespace(realm_key="zhuji", stage_key="late"),
    ]
    dist = svc._realm_distribution(chars)
    assert dist[("lianqi", "early")] == 2
    assert dist[("lianqi", "mid")] == 1
    assert dist[("zhuji", "late")] == 1
    assert isinstance(dist, Counter)


def test_roll_lingshi_respects_max() -> None:
    """_roll_lingshi 在大量样本下永远不超 max_lingshi。"""
    from bot.services.npc_service import NpcService

    svc = NpcService.__new__(NpcService)
    svc.rng = random.Random(42)

    max_l = 1000
    samples = [svc._roll_lingshi(max_l) for _ in range(500)]
    assert max(samples) <= max_l
    assert min(samples) >= 0


def test_roll_lingshi_returns_zero_when_max_is_zero() -> None:
    from bot.services.npc_service import NpcService

    svc = NpcService.__new__(NpcService)
    svc.rng = random.Random(42)
    assert svc._roll_lingshi(0) == 0


def test_roll_artifact_growth_total_matches_expected() -> None:
    """三维投点之和 = growth_total × reinforce_level。"""
    from bot.services.artifact_service import ArtifactService
    from bot.services.npc_service import NpcService

    rng = random.Random(42)
    svc = NpcService.__new__(NpcService)
    svc.rng = rng
    svc.artifact_service = ArtifactService(rng)

    stage = get_stage("lianqi", "early")
    growth_total = svc.artifact_service._growth_total(stage)
    reinforce = 5

    atk, df, agi = svc._roll_artifact_growth(stage, reinforce)
    assert atk + df + agi == growth_total * reinforce
    assert atk >= 0 and df >= 0 and agi >= 0


def test_roll_artifact_growth_zero_level_returns_zeros() -> None:
    from bot.services.artifact_service import ArtifactService
    from bot.services.npc_service import NpcService

    rng = random.Random(42)
    svc = NpcService.__new__(NpcService)
    svc.rng = rng
    svc.artifact_service = ArtifactService(rng)

    stage = get_stage("lianqi", "early")
    assert svc._roll_artifact_growth(stage, 0) == (0, 0, 0)


# ---------------------------------------------------------------------------
# cascade 删除：删 player 自动清 character / artifact / ladder_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_cascade_delete_via_player(
    session_factory, services
) -> None:
    """删除 NPC 的 Player 应自动级联清掉 Character / Artifact / LadderRecord。"""
    async with session_factory() as session:
        await _create_real_player(session, services, discord_id=9001, name="真人甲")
        await session.flush()

        await services.npc.ensure_daily_pool(session)
        await session.flush()

        npcs = (
            await session.scalars(
                select(Character).where(Character.is_npc.is_(True))
            )
        ).all()
        # 取一只 NPC 演示 cascade
        target = npcs[0]
        target_player_id = target.player_id
        target_artifact_id = target.artifact.id
        target_ladder_id = target.ladder_record.id

        player = await session.get(Player, target_player_id)
        assert player is not None
        await session.delete(player)
        await session.flush()

        # 验证 cascade 链
        assert await session.get(Player, target_player_id) is None
        assert await session.get(Character, target.id) is None
        assert await session.get(Artifact, target_artifact_id) is None
        assert await session.get(LadderRecord, target_ladder_id) is None
