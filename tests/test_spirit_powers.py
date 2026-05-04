from __future__ import annotations

import json

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.data.spirits import SPIRIT_POWER_DEFINITIONS, SpiritPowerEntry, get_spirit_power_definition


class CombatRoller:
    def __init__(self, random_values, *, fallback: float = 0.99) -> None:
        self._random_values = iter(random_values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._random_values, self._fallback)


def test_spirit_power_pool_expands_to_twenty_entries() -> None:
    power_ids = {definition.power_id for definition in SPIRIT_POWER_DEFINITIONS}

    assert len(SPIRIT_POWER_DEFINITIONS) == 23
    assert {"shisheng", "jueming", "xuanjia", "fanji", "guifeng", "niepan", "jinmai", "xuekuang"} <= power_ids
    assert {"fenmai", "luejie", "chengshi", "lingyong", "zhuying", "huajing", "duofeng", "zhenling"} <= power_ids
    assert {"chunsheng", "suijue", "mingche", "zhuifeng"} <= power_ids
    # 新增神通
    assert {"leifa", "shiyan", "fengdun"} <= power_ids


@pytest.mark.asyncio
async def test_existing_spirit_json_remains_compatible_after_pool_expansion(session_factory, services) -> None:
    async with session_factory() as session:
        creation = await services.character.get_or_create_character(session, 6101, "旧灵")
        artifact = creation.character.artifact
        artifact.reinforce_level = 30
        artifact.spirit_name = "旧灵"
        artifact.spirit_json = json.dumps(
            {
                "tier": "high",
                "stats": [
                    {"stat": "atk", "kind": "flat", "value": 3200},
                    {"stat": "def", "kind": "ratio", "value": 24},
                    {"stat": "agi", "kind": "ratio", "value": 18},
                ],
                "power": {"power_id": "niepan", "rolls": {"heal_pct": 42, "reduce_pct": 72}},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        snapshot = services.character.build_snapshot(creation.character)
        await session.commit()

        assert snapshot.spirit_name == "旧灵"
        assert snapshot.spirit_power_name == "涅槃"
        assert services.spirit.get_current_spirit(artifact) is not None


def test_spirit_power_description_accepts_legacy_rolls() -> None:
    description = get_spirit_power_definition("niepan").describe({"heal_pct": 42})

    assert "守势" in description


def test_shisheng_can_heal_from_zhuohun_burn_damage(services) -> None:
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"proc_pct": 100, "burn_pct": 20, "scar_bonus_pct": 0})
    roller = CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99])

    attacker_without_spirit = services.combat.create_combatant(
        name="焚者",
        atk=85,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
    )
    attacker_with_spirit = services.combat.create_combatant(
        name="焚者",
        atk=85,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("shisheng", {"heal_pct": 100}),
    )
    defender = services.combat.create_combatant(name="枯木", atk=30, defense=10, agility=10)

    baseline = services.combat.run_battle(attacker_without_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker_with_spirit, defender, rng=roller)

    assert empowered.challenger_hp_after > baseline.challenger_hp_after
    assert any(log.text and "噬生吞回血气" in log.text for log in empowered.logs)


def test_fenmai_triggers_extra_damage_on_burning_target(services) -> None:
    burn_affix = ArtifactAffixEntry(slot=1, affix_id="zhuohun", rolls={"proc_pct": 100, "burn_pct": 10, "scar_bonus_pct": 0})

    attacker_without_spirit = services.combat.create_combatant(
        name="烬心",
        atk=70,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
    )
    attacker_with_spirit = services.combat.create_combatant(
        name="烬心",
        atk=70,
        defense=10,
        agility=50,
        affixes=(burn_affix,),
        spirit_power=SpiritPowerEntry("fenmai", {"ignite_pct": 100}),
    )
    defender = services.combat.create_combatant(name="荒甲", atk=25, defense=10, agility=10)

    baseline = services.combat.run_battle(attacker_without_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker_with_spirit, defender, rng=CombatRoller([0.99, 0.99, 0.0]))

    assert empowered.challenger_hp_after > baseline.challenger_hp_after
    assert any(log.text and "焚脉" in log.text for log in empowered.logs)


def test_huajing_converts_reduction_affix_into_recovery(services) -> None:
    reduce_affix = ArtifactAffixEntry(slot=1, affix_id="zhenmai", rolls={"reduce_pct": 50})
    services.combat.max_rounds = 1
    attacker = services.combat.create_combatant(name="破锋", atk=150, defense=10, agility=40)
    defender_without_spirit = services.combat.create_combatant(
        name="守川",
        atk=20,
        defense=10,
        agility=10,
        affixes=(reduce_affix,),
    )
    defender_with_spirit = services.combat.create_combatant(
        name="守川",
        atk=20,
        defense=10,
        agility=10,
        affixes=(reduce_affix,),
        spirit_power=SpiritPowerEntry("huajing", {"convert_pct": 100}),
    )

    baseline = services.combat.run_battle(attacker, defender_without_spirit, rng=CombatRoller([0.99, 0.99, 0.99, 0.99]))
    empowered = services.combat.run_battle(attacker, defender_with_spirit, rng=CombatRoller([0.99, 0.99, 0.99, 0.99]))

    assert empowered.defender_hp_after > baseline.defender_hp_after
    assert any(log.text and "化劲" in log.text for log in empowered.logs)
