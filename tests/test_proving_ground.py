"""证道战场核心逻辑测试。"""

from __future__ import annotations

import random

import pytest

from bot.data.proving_ground import (
    PG_BASE_STATS,
    PG_BOSS_PRESET,
    PG_BOSS_SELF,
    PG_BOSS_STRONGEST,
    PG_ELITE_GUARANTEED_PER_MAP,
    PG_ENTRY_QI_COST,
    PG_MAP_LAYERS,
    PG_NODE_TYPE_BOSS,
    PG_NODE_TYPE_ELITE,
    PG_NODE_TYPE_EVENT,
    PG_NODE_TYPE_NORMAL,
    PG_NODE_TYPE_START,
    PG_SCORE_NORMAL_KILL,
    PG_STATUS_COMPLETED,
    PG_STATUS_FAILED,
)
from bot.services.combat_service import CombatService
from bot.services.proving_ground_service import (
    MAX_AFFIX_SLOTS,
    PGBuild,
    PGMap,
    MapNode,
    ProvingGroundService,
)


@pytest.fixture
def rng() -> random.Random:
    return random.Random(42)


@pytest.fixture
def combat_service(rng: random.Random) -> CombatService:
    return CombatService(rng=rng)


@pytest.fixture
def pg_service(combat_service: CombatService, rng: random.Random) -> ProvingGroundService:
    return ProvingGroundService(combat_service=combat_service, rng=rng)


# ---------------------------------------------------------------------------
# 地图生成
# ---------------------------------------------------------------------------


class TestMapGeneration:
    def test_map_has_start_and_boss(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        types = {n.node_type for n in m.nodes}
        assert PG_NODE_TYPE_START in types
        assert PG_NODE_TYPE_BOSS in types

    def test_map_node_count_in_range(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        # start + 6 layers * (2-3 nodes) + boss = 15~21
        assert 15 <= len(m.nodes) <= 21

    def test_map_layers_correct(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        layers = {n.layer for n in m.nodes}
        # layer 0 (start) through PG_MAP_LAYERS+1 (boss)
        assert 0 in layers
        assert (PG_MAP_LAYERS + 1) in layers

    def test_map_has_guaranteed_elites(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        elite_count = sum(1 for n in m.nodes if n.node_type == PG_NODE_TYPE_ELITE)
        assert elite_count >= PG_ELITE_GUARANTEED_PER_MAP

    def test_map_all_next_layer_nodes_reachable(self, pg_service: ProvingGroundService):
        """每一层的每个节点至少有 1 条入边。"""
        m = pg_service.generate_map()
        connected: set[int] = {0}  # start is reachable
        for n in m.nodes:
            for c in n.connections:
                connected.add(c)
        for n in m.nodes:
            if n.node_type != PG_NODE_TYPE_START:
                assert n.node_id in connected, f"Node {n.node_id} L{n.layer} unreachable"

    def test_map_boss_has_no_connections(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        boss = m.get_node(m.boss_node_id)
        assert boss is not None
        assert boss.connections == ()

    def test_map_last_layer_all_connect_to_boss(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        last_layer = m.get_layer_nodes(PG_MAP_LAYERS)
        for n in last_layer:
            assert m.boss_node_id in n.connections

    def test_map_serialization_roundtrip(self, pg_service: ProvingGroundService):
        m = pg_service.generate_map()
        raw = pg_service.serialize_map(m)
        m2 = pg_service.deserialize_map(raw)
        assert len(m2.nodes) == len(m.nodes)
        assert m2.boss_node_id == m.boss_node_id
        for a, b in zip(m.nodes, m2.nodes):
            assert a.node_id == b.node_id
            assert a.node_type == b.node_type
            assert a.connections == b.connections


# ---------------------------------------------------------------------------
# 构筑系统
# ---------------------------------------------------------------------------


class TestBuildSystem:
    def test_initial_build_matches_base_stats(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        assert build.atk == PG_BASE_STATS["atk"]
        assert build.defense == PG_BASE_STATS["def"]
        assert build.agility == PG_BASE_STATS["agi"]

    def test_effective_stats_with_bonus(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        build.atk_pct_bonus = 50  # +50%
        assert build.effective_atk() == int(PG_BASE_STATS["atk"] * 150 / 100)

    def test_affix_pick_adds_to_build(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        choices = pg_service.roll_affix_choices(3)
        assert len(choices) == 3
        pg_service.apply_affix_pick(build, choices[0])
        assert len(build.affixes) == 1
        assert build.affixes[0].affix_id == choices[0].affix_id

    def test_affix_slot_limit(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        for _ in range(MAX_AFFIX_SLOTS + 2):
            choices = pg_service.roll_affix_choices(1)
            pg_service.apply_affix_pick(build, choices[0])
        assert len(build.affixes) == MAX_AFFIX_SLOTS

    def test_reroll_affix_takes_high(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        choices = pg_service.roll_affix_choices(1)
        pg_service.apply_affix_pick(build, choices[0])
        old_rolls = dict(build.affixes[0].rolls)
        # reroll many times to ensure at least one value improves or stays
        for _ in range(10):
            pg_service.reroll_affix(build, 0)
        for key in old_rolls:
            assert build.affixes[0].rolls[key] >= old_rolls[key]

    def test_spirit_roll_and_reroll(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        assert build.spirit_power is None
        msg, power = pg_service.roll_new_spirit(build)
        assert build.spirit_power is not None
        old_rolls = dict(build.spirit_power.rolls)
        for _ in range(10):
            pg_service.reroll_spirit(build)
        for key in old_rolls:
            assert build.spirit_power.rolls[key] >= old_rolls[key]

    def test_build_serialization_roundtrip(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        choices = pg_service.roll_affix_choices(2)
        pg_service.apply_affix_pick(build, choices[0])
        pg_service.apply_affix_pick(build, choices[1])
        pg_service.roll_new_spirit(build)
        build.atk_pct_bonus = 20

        raw = pg_service.serialize_build(build)
        b2 = pg_service.deserialize_build(raw)
        assert b2.atk == build.atk
        assert b2.atk_pct_bonus == 20
        assert len(b2.affixes) == 2
        assert b2.spirit_power is not None
        assert b2.spirit_power.power_id == build.spirit_power.power_id

    def test_player_snapshot_from_build(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        build.atk_pct_bonus = 10
        choices = pg_service.roll_affix_choices(1)
        pg_service.apply_affix_pick(build, choices[0])
        snap = pg_service.build_player_snapshot(build, "TestPlayer")
        assert snap.name == "TestPlayer"
        assert snap.atk == build.effective_atk()
        assert len(snap.affixes) == 1


# ---------------------------------------------------------------------------
# 敌人生成
# ---------------------------------------------------------------------------


class TestEnemyGeneration:
    def test_normal_enemy_layer1_no_affixes(self, pg_service: ProvingGroundService):
        _, enemy = pg_service.generate_normal_enemy(1)
        assert len(enemy.affixes) == 0
        assert enemy.atk > 0

    def test_normal_enemy_layer5_has_affixes(self, pg_service: ProvingGroundService):
        _, enemy = pg_service.generate_normal_enemy(5)
        assert len(enemy.affixes) == 2

    def test_elite_enemy_has_spirit_and_extra_affix(self, pg_service: ProvingGroundService):
        _, enemy = pg_service.generate_elite_enemy(3)
        # layer 3 normal = 1 affix, elite = 1+1 = 2
        assert len(enemy.affixes) == 2
        assert enemy.spirit_power is not None

    def test_elite_stronger_than_normal(self, pg_service: ProvingGroundService):
        rng = random.Random(99)
        cs = CombatService(rng=rng)
        pgs = ProvingGroundService(combat_service=cs, rng=rng)
        _, normal = pgs.generate_normal_enemy(3)
        _, elite = pgs.generate_elite_enemy(3)
        assert elite.atk > normal.atk

    def test_boss_preset_has_full_affixes(self, pg_service: ProvingGroundService):
        _, boss = pg_service.generate_boss_preset()
        assert len(boss.affixes) == 5
        assert boss.spirit_power is not None

    def test_boss_preset_max_rolled(self, pg_service: ProvingGroundService):
        """天劫化身的词条应为满 roll。"""
        from bot.data.artifact_affixes import ARTIFACT_AFFIX_DEFINITIONS
        _, boss = pg_service.generate_boss_preset()
        for affix_entry in boss.affixes:
            defn = pg_service._get_affix_definition(affix_entry.affix_id)
            assert defn is not None
            for key, _low, high in defn.roll_ranges:
                assert affix_entry.rolls[key] == high


# ---------------------------------------------------------------------------
# 事件系统
# ---------------------------------------------------------------------------


class TestEventSystem:
    def test_event_generation_covers_categories(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        # 加点东西让更多事件可触发
        choices = pg_service.roll_affix_choices(2)
        pg_service.apply_affix_pick(build, choices[0])
        pg_service.apply_affix_pick(build, choices[1])
        pg_service.roll_new_spirit(build)

        categories_seen: set[str] = set()
        for _ in range(100):
            event = pg_service.generate_event(build)
            categories_seen.add(event.category)
        # 所有 5 种类别都应出现
        assert categories_seen == {"attribute", "affix", "spirit", "mixed", "easter_egg"}

    def test_attribute_event_applies(self, pg_service: ProvingGroundService):
        build = pg_service.create_initial_build()
        from bot.services.proving_ground_service import PGEvent, PGEventChoice
        from bot.models.proving_ground_run import ProvingGroundRun
        from bot.models.character import Character

        event = PGEvent(
            event_id="tiandi_lingyun",
            name="天地灵韵",
            description="",
            category="attribute",
            auto_apply=True,
        )
        run = ProvingGroundRun()
        run.score = 0
        run.pending_affix_ops = 0
        run.pending_spirit_ops = 0
        char = Character()
        char.pg_red_dust_count = 0

        result = pg_service.apply_event(event, "", build, run, char)
        assert result.success
        assert build.atk_pct_bonus == 8
        assert build.def_pct_bonus == 8
        assert build.agi_pct_bonus == 8

    def test_duyun_gamble_outcomes(self):
        """赌运事件的两种结果。"""
        from bot.services.proving_ground_service import PGEvent, PGBuild
        from bot.models.proving_ground_run import ProvingGroundRun
        from bot.models.character import Character

        wins = 0
        losses = 0
        for seed in range(200):
            rng = random.Random(seed)
            cs = CombatService(rng=rng)
            pgs = ProvingGroundService(combat_service=cs, rng=rng)
            build = pgs.create_initial_build()
            event = PGEvent(
                event_id="duyun", name="赌运", description="", category="mixed",
            )
            run = ProvingGroundRun()
            run.score = 0
            run.pending_affix_ops = 0
            run.pending_spirit_ops = 0
            char = Character()
            char.pg_red_dust_count = 0
            result = pgs.apply_event(event, "gamble", build, run, char)
            if build.atk_pct_bonus > 0:
                wins += 1
            else:
                losses += 1
        # 应大致 50/50
        assert wins > 50
        assert losses > 50

    def test_hongchen_lijie_increments(self, pg_service: ProvingGroundService):
        from bot.services.proving_ground_service import PGEvent
        from bot.models.proving_ground_run import ProvingGroundRun
        from bot.models.character import Character

        build = pg_service.create_initial_build()
        event = PGEvent(
            event_id="hongchen_lijie", name="红尘历劫", description="", category="easter_egg", auto_apply=True,
        )
        run = ProvingGroundRun()
        run.score = 0
        run.pending_affix_ops = 0
        run.pending_spirit_ops = 0
        char = Character()
        char.pg_red_dust_count = 0

        for i in range(9):
            pg_service.apply_event(event, "", build, run, char)
        assert char.pg_red_dust_count == 9


# ---------------------------------------------------------------------------
# BOSS 类型选择
# ---------------------------------------------------------------------------


class TestBossTypePick:
    def test_first_three_runs_different_bosses(self, pg_service: ProvingGroundService):
        from bot.models.character import Character
        char = Character()
        char.pg_boss_kills_json = "[]"
        bosses = []
        for boss_type in [PG_BOSS_PRESET, PG_BOSS_STRONGEST, PG_BOSS_SELF]:
            picked = pg_service.pick_boss_type(char)
            bosses.append(picked)
            char.add_pg_boss_kill(picked)
        # 前 3 次应各不同
        assert len(set(bosses)) == 3

    def test_after_all_killed_random(self, pg_service: ProvingGroundService):
        from bot.models.character import Character
        import json
        char = Character()
        char.pg_boss_kills_json = json.dumps(["preset", "strongest", "self"])
        # 多次调用应该不报错
        for _ in range(20):
            picked = pg_service.pick_boss_type(char)
            assert picked in (PG_BOSS_PRESET, PG_BOSS_STRONGEST, PG_BOSS_SELF)


# ---------------------------------------------------------------------------
# 节点战斗
# ---------------------------------------------------------------------------


class TestNodeCombat:
    def test_normal_combat_yields_score(self, pg_service: ProvingGroundService):
        from bot.models.proving_ground_run import ProvingGroundRun
        build = pg_service.create_initial_build()
        # 大幅增强确保胜利
        build.atk_pct_bonus = 200
        build.def_pct_bonus = 200
        build.agi_pct_bonus = 200
        node = MapNode(node_id=1, layer=1, node_type=PG_NODE_TYPE_NORMAL, connections=())
        run = ProvingGroundRun()
        run.score = 0
        run.pending_affix_ops = 0
        run.pending_spirit_ops = 0
        result = pg_service.run_node_combat(node, build, "TestPlayer", run)
        assert result.success
        if result.victory:
            assert result.score_gained >= PG_SCORE_NORMAL_KILL
            assert run.score >= PG_SCORE_NORMAL_KILL
