"""证道战场 BOSS 选取与快照序列化测试。"""

from __future__ import annotations

import json
import random

from bot.data.artifact_affixes import ARTIFACT_AFFIX_DEFINITIONS, ArtifactAffixEntry
from bot.data.proving_ground_enemies import PG_BOSS_PRESET_NAME
from bot.data.spirits import SPIRIT_POWER_DEFINITIONS, SPIRIT_TIER_ORDER
from bot.services.combat_service import CombatantSnapshot
from bot.services.proving_ground_service import ProvingGroundService


def _service(services) -> ProvingGroundService:
    return ProvingGroundService(services.combat, random.Random(42))


def _make_snapshot() -> CombatantSnapshot:
    defn = ARTIFACT_AFFIX_DEFINITIONS[0]
    affix = ArtifactAffixEntry(slot=0, affix_id=defn.affix_id, rolls=defn.roll(random.Random(1)))
    spirit = SPIRIT_POWER_DEFINITIONS[0].roll(SPIRIT_TIER_ORDER[0], random.Random(2))
    return CombatantSnapshot(
        name="道心投影·测试",
        atk=3_000_000,
        defense=2_400_000,
        agility=1_900_000,
        max_hp=24_000_000,
        affixes=(affix,),
        spirit_power=spirit,
        realm_index=9,
        damage_dealt_basis_points=500,
        damage_taken_basis_points=-300,
        damage_reduction_basis_points=200,
        versus_higher_realm_damage_basis_points=800,
        base_resilience=32,
    )


# ---------------------------------------------------------------------------
# 快照序列化
# ---------------------------------------------------------------------------


def test_boss_snapshot_roundtrip_preserves_affixes_and_spirit(services) -> None:
    pg = _service(services)
    snap = _make_snapshot()
    restored = pg.deserialize_boss_snapshot(pg.serialize_boss_snapshot(snap))
    assert restored == snap


def test_deserialize_legacy_five_field_snapshot() -> None:
    raw = json.dumps(
        {"name": "道心投影·旧数据", "atk": 1, "defense": 2, "agility": 3, "max_hp": 4}
    )
    snap = ProvingGroundService.deserialize_boss_snapshot(raw)
    assert snap is not None
    assert snap.name == "道心投影·旧数据"
    assert snap.affixes == ()
    assert snap.spirit_power is None
    assert snap.base_resilience == 0


def test_deserialize_boss_snapshot_rejects_invalid_payload() -> None:
    assert ProvingGroundService.deserialize_boss_snapshot(None) is None
    assert ProvingGroundService.deserialize_boss_snapshot("") is None
    assert ProvingGroundService.deserialize_boss_snapshot("{}") is None
    assert ProvingGroundService.deserialize_boss_snapshot("not-json") is None
    assert ProvingGroundService.deserialize_boss_snapshot('{"name": "缺字段"}') is None


def test_generate_boss_preset_supports_custom_name(services) -> None:
    pg = _service(services)
    name, _ = pg.generate_boss_preset(name="道心投影")
    assert name == "道心投影"
    default_name, _ = pg.generate_boss_preset()
    assert default_name == PG_BOSS_PRESET_NAME


# ---------------------------------------------------------------------------
# 道心投影原型选取
# ---------------------------------------------------------------------------


async def _create_character(session, services, discord_id: str, name: str):
    result = await services.character.get_or_create_character(session, discord_id, name)
    return result.character


async def test_pick_projection_excludes_npc_weixian_and_self(session_factory, services) -> None:
    async with session_factory() as session:
        entrant = await _create_character(session, services, "100", "entrant")
        entrant.realm_key, entrant.stage_key = "dujie", "perfect"
        rival = await _create_character(session, services, "101", "rival")
        rival.realm_key, rival.stage_key = "dujie", "perfect"
        npc = await _create_character(session, services, "102", "npc")
        npc.realm_key, npc.stage_key = "dujie", "perfect"
        npc.is_npc = True
        immortal = await _create_character(session, services, "103", "immortal")
        immortal.realm_key, immortal.stage_key = "weixian", "perfect"
        for char in (entrant, rival, npc, immortal):
            services.character.refresh_combat_power(char)
        await session.commit()

        picked = ProvingGroundService.pick_projection_character(
            [entrant, rival, npc, immortal], entrant, services.character
        )
        assert picked is not None
        assert picked.id == rival.id


async def test_pick_projection_returns_none_without_dujie_real_player(
    session_factory, services
) -> None:
    async with session_factory() as session:
        entrant = await _create_character(session, services, "200", "entrant")
        entrant.realm_key, entrant.stage_key = "weixian", "perfect"
        npc = await _create_character(session, services, "201", "npc")
        npc.realm_key, npc.stage_key = "dujie", "perfect"
        npc.is_npc = True
        for char in (entrant, npc):
            services.character.refresh_combat_power(char)
        await session.commit()

        picked = ProvingGroundService.pick_projection_character(
            [entrant, npc], entrant, services.character
        )
        assert picked is None


async def test_pick_projection_picks_strongest_dujie(session_factory, services) -> None:
    async with session_factory() as session:
        entrant = await _create_character(session, services, "300", "entrant")
        entrant.realm_key, entrant.stage_key = "dujie", "perfect"
        weak = await _create_character(session, services, "301", "weak")
        weak.realm_key, weak.stage_key = "dujie", "early"
        strong = await _create_character(session, services, "302", "strong")
        strong.realm_key, strong.stage_key = "dujie", "perfect"
        for char in (entrant, weak, strong):
            services.character.refresh_combat_power(char)
        await session.commit()

        picked = ProvingGroundService.pick_projection_character(
            [entrant, weak, strong], entrant, services.character
        )
        assert picked is not None
        assert picked.id == strong.id
