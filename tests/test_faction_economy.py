from __future__ import annotations

from datetime import datetime

import pytest

from bot.data.realms import get_stage
from bot.services.combat_service import BattleResult
from bot.services.faction_service import FactionService, INFAMY_BY_REALM
from bot.services.npc_service import NpcService
from bot.utils.time_utils import SHANGHAI


def _battle(*, challenger_won: bool) -> BattleResult:
    winner, loser = ("甲", "乙") if challenger_won else ("乙", "甲")
    return BattleResult(
        challenger_won=challenger_won,
        winner_name=winner,
        loser_name=loser,
        rounds=1,
        reached_round_limit=False,
        logs=[],
        challenger_max_hp=100,
        defender_max_hp=100,
        challenger_hp_after=100 if challenger_won else 0,
        defender_hp_after=0 if challenger_won else 100,
    )


@pytest.fixture
def faction(services) -> FactionService:
    return FactionService(services.character, services.combat)


def test_realm_loot_multiplier_steps() -> None:
    assert FactionService.realm_loot_multiplier_pct(3, 3) == 100
    assert FactionService.realm_loot_multiplier_pct(3, 2) == 70
    assert FactionService.realm_loot_multiplier_pct(3, 1) == 40
    assert FactionService.realm_loot_multiplier_pct(10, 1) == 0
    assert FactionService.realm_loot_multiplier_pct(3, 4) == 130
    assert FactionService.realm_loot_multiplier_pct(9, 10) == 130


@pytest.mark.asyncio
async def test_robbery_win_adds_infamy_then_bounty(session_factory, services, faction) -> None:
    async with session_factory() as session:
        robber = (await services.character.get_or_create_character(session, 8001, "魔修")).character
        target = (await services.character.get_or_create_character(session, 8002, "肥羊")).character
        robber.faction = "demonic"
        target.realm_key = "jiedan"
        target.realm_index = 3
        target.lingshi = 10_000
        target.artifact.soul_shards = 0
        robber.artifact.soul_shards = 0
        services.combat.run_battle = lambda *args, **kwargs: _battle(challenger_won=True)

        result = faction.rob(robber, target)

        assert result.success
        assert robber.infamy == 200
        assert robber.bounty_soul == 20
        assert result.infamy_delta == 200


@pytest.mark.asyncio
async def test_higher_realm_robbing_lower_gets_zero_loot(session_factory, services, faction) -> None:
    async with session_factory() as session:
        robber = (await services.character.get_or_create_character(session, 8011, "伪仙魔")).character
        target = (await services.character.get_or_create_character(session, 8012, "炼气羊")).character
        robber.faction = "demonic"
        robber.realm_key = "weixian"
        robber.realm_index = 10
        target.realm_key = "lianqi"
        target.realm_index = 1
        target.lingshi = 80_000
        target.artifact.soul_shards = 90_000
        robber.artifact.soul_shards = 0
        services.combat.run_battle = lambda *args, **kwargs: _battle(challenger_won=True)

        result = faction.rob(robber, target)

        assert result.success
        assert result.soul_delta == 0
        assert result.lingshi_delta == 0
        assert target.lingshi == 80_000
        assert robber.infamy == 50


@pytest.mark.asyncio
async def test_bounty_payout_scales_with_realm_gap(session_factory, services, faction) -> None:
    async with session_factory() as session:
        hunter = (await services.character.get_or_create_character(session, 8021, "正道")).character
        target = (await services.character.get_or_create_character(session, 8022, "魔修")).character
        hunter.faction = "righteous"
        hunter.realm_index = 3
        target.faction = "demonic"
        target.realm_index = 3
        target.bounty_soul = 100
        hunter.artifact.soul_shards = 0
        services.combat.run_battle = lambda *args, **kwargs: _battle(challenger_won=True)

        result = faction.challenge_bounty(hunter, target)

        assert result.success
        assert result.soul_delta == 100
        assert result.lingshi_delta == 500
        assert hunter.virtue == 100
        assert hunter.luck >= 10
        assert target.bounty_soul == 0
        assert target.infamy == 0


@pytest.mark.asyncio
async def test_npc_uses_realm_cap_and_matching_affix_slots(session_factory, services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    async with session_factory() as session:
        await services.character.get_or_create_character(session, 8031, "样本")
        spawned = await npc_service.ensure_daily_pool(session, now=datetime(2026, 5, 14, 0, 5, tzinfo=SHANGHAI))
        await session.flush()

        assert spawned == 30
        npcs = await npc_service._load_npcs(session)
        assert len(npcs) == 30
        assert any(npc.faction == "demonic" and npc.bounty_soul > 0 for npc in npcs)
        allowed = {
            "lianqi", "zhuji", "jiedan",
            "yuanying", "huashen", "lianxu",
            "heti", "dacheng", "dujie",
            "weixian",
        }
        for npc in npcs:
            stage = get_stage(npc.realm_key, npc.stage_key)
            assert npc.realm_key in allowed
            assert npc.stage_key in ("early", "mid", "late", "perfect")
            assert npc.artifact.reinforce_level == stage.reinforce_cap
            assert len(services.artifact.get_affix_slots(npc.artifact)) == services.artifact.unlocked_slots(npc.artifact)


@pytest.mark.asyncio
async def test_npc_top_up_replaces_emptied_demonic(session_factory, services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    noon = datetime(2026, 5, 14, 12, 0, tzinfo=SHANGHAI)
    async with session_factory() as session:
        await services.character.get_or_create_character(session, 8041, "样本")
        await npc_service.ensure_daily_pool(session, now=datetime(2026, 5, 14, 0, 1, tzinfo=SHANGHAI))
        npcs = await npc_service._load_npcs(session)
        demonic = [npc for npc in npcs if npc.faction == "demonic"]
        assert demonic
        emptied = demonic[: max(1, len(demonic) // 2)]
        for npc in emptied:
            npc.bounty_soul = 0
        await session.flush()

        added = await npc_service.ensure_daily_pool(session, now=noon)
        refreshed = await npc_service._load_npcs(session)
        live_demonic = [npc for npc in refreshed if npc.faction == "demonic" and npc.bounty_soul > 0]

        assert added == len(emptied)
        assert len(refreshed) == npc_service.DAILY_POOL_SIZE
        assert len(live_demonic) == len(demonic)


@pytest.mark.asyncio
async def test_npc_top_up_does_not_grow_pool_when_demonic_below_half(session_factory, services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    noon = datetime(2026, 5, 14, 12, 0, tzinfo=SHANGHAI)
    async with session_factory() as session:
        await services.character.get_or_create_character(session, 8042, "样本")
        await npc_service.ensure_daily_pool(session, now=datetime(2026, 5, 14, 0, 1, tzinfo=SHANGHAI))
        npcs = await npc_service._load_npcs(session)
        demonic = [npc for npc in npcs if npc.faction == "demonic"]
        flipped = demonic[: max(0, len(demonic) - 8)]
        for npc in flipped:
            npc.faction = "neutral"
            npc.bounty_soul = 0
        leftover = [npc for npc in demonic if npc not in flipped]
        assert leftover
        emptied = leftover[:3]
        for npc in emptied:
            npc.bounty_soul = 0
        await session.flush()

        added = await npc_service.ensure_daily_pool(session, now=noon)
        refreshed = await npc_service._load_npcs(session)

        assert added == len(emptied)
        assert len(refreshed) == npc_service.DAILY_POOL_SIZE


def test_npc_bounty_uses_infamy_floor_when_server_has_none(services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    assert npc_service._roll_bounty(0, realm_key="jiedan", lo=0.01, hi=0.05) == INFAMY_BY_REALM["jiedan"]
    rolled = npc_service._roll_bounty(3000, realm_key="jiedan", lo=0.01, hi=0.05)
    assert 30 <= rolled <= 150


def test_npc_soul_and_bounty_follow_realm_band(services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    soul = npc_service._roll_soul_shards(100_000, lo=0.01, hi=0.05)
    assert 1000 <= soul <= 5000
    weixian = npc_service._roll_soul_shards(100_000, lo=0.15, hi=0.20)
    assert 15000 <= weixian <= 20000


@pytest.mark.asyncio
async def test_spawned_npc_soul_stays_within_realm_band(session_factory, services) -> None:
    npc_service = NpcService(
        services.fate.rng,
        services.character,
        services.fate,
        services.artifact,
        services.spirit,
    )
    low = {"lianqi", "zhuji", "jiedan"}
    mid = {"yuanying", "huashen", "lianxu"}
    high = {"heti", "dacheng", "dujie"}
    async with session_factory() as session:
        rich = (await services.character.get_or_create_character(session, 8051, "首富")).character
        second = (await services.character.get_or_create_character(session, 8052, "第二")).character
        rich.artifact.soul_shards = 250_000
        second.artifact.soul_shards = 100_000
        rich.infamy = 3000
        await session.flush()
        await npc_service.ensure_daily_pool(session, now=datetime(2026, 5, 14, 0, 5, tzinfo=SHANGHAI))
        npcs = await npc_service._load_npcs(session)

    for npc in npcs:
        soul = npc.artifact.soul_shards
        if npc.realm_key in low:
            assert 1000 <= soul <= 5000
        elif npc.realm_key in mid:
            assert 5000 <= soul <= 10000
        elif npc.realm_key in high:
            assert 10000 <= soul <= 15000
        else:
            assert npc.realm_key == "weixian"
            assert 15000 <= soul <= 20000
        if npc.faction == "demonic":
            if npc.realm_key in low:
                assert 30 <= npc.bounty_soul <= 150
            elif npc.realm_key in mid:
                assert 150 <= npc.bounty_soul <= 300
            elif npc.realm_key in high:
                assert 300 <= npc.bounty_soul <= 450
            else:
                assert 450 <= npc.bounty_soul <= 600
