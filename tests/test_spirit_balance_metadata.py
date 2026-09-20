from __future__ import annotations

import json
import random

import pytest

from bot.data.spirits import (
    LINGYONG_CLEANSE_STACKS_PER_DEBUFF,
    LINGYONG_NORMAL_STACK_CAP,
    LINGYONG_STACK_CAP,
    LINGYONG_TRUE_DAMAGE_ATK_CAP_PCT,
    LINGYONG_TRUE_DAMAGE_PER_STACK_PCT,
    SPIRIT_TIER_BY_KEY,
    SPIRIT_TIER_ORDER,
    get_spirit_power_definition,
)
from bot.models.artifact import Artifact
from bot.models.proving_ground_run import ProvingGroundRun
from bot.services.proving_ground_service import PGBuild, ProvingGroundService
from bot.services.spirit_service import SpiritService


_JUEMING_TIERS = [
    ("low", 8, (1, 5), (1, 5)),
    ("mid", 7, (5, 10), (5, 10)),
    ("high", 6, (15, 25), (10, 15)),
    ("peak", 5, (25, 30), (15, 20)),
    ("supreme", 4, (33, 35), (20, 25)),
]
_LINGYONG_TIERS = [
    ("low", 1, (2, 3)),
    ("mid", 2, (3, 5)),
    ("high", 3, (5, 7)),
    ("peak", 4, (7, 10)),
    ("supreme", 5, (10, 14)),
]


def _spirit_payload(tier: str, power_id: str, rolls: dict) -> dict:
    return {
        "tier": tier,
        "stats": [
            {"stat": stat, "kind": "flat", "value": 1}
            for stat in ("atk", "def", "agi")
        ],
        "power": {"power_id": power_id, "rolls": dict(rolls)},
    }


@pytest.mark.parametrize(("tier", "cost", "hp_range", "heal_range"), _JUEMING_TIERS)
def test_jueming_generation_and_description_use_fixed_tier_cost(
    tier, cost, hp_range, heal_range, monkeypatch,
) -> None:
    definition = get_spirit_power_definition("jueming")
    assert definition.roll_ranges_by_tier[tier] == (
        ("omen_cost", cost, cost),
        ("hp_pct", *hp_range),
        ("heal_down_pct", *heal_range),
    )
    service = SpiritService(random.Random(42))
    monkeypatch.setattr(service, "_roll_tier", lambda: SPIRIT_TIER_BY_KEY[tier])
    artifact = Artifact(atk_bonus=100, def_bonus=100, agi_bonus=100)

    for _ in range(20):
        spirit = service._roll_spirit(artifact, power_definition=definition)
        assert spirit.tier == tier
        assert spirit.power.rolls["omen_cost"] == cost
        assert hp_range[0] <= spirit.power.rolls["hp_pct"] <= hp_range[1]
        assert heal_range[0] <= spirit.power.rolls["heal_down_pct"] <= heal_range[1]
        assert f"\u2265{cost}" in definition.describe(spirit.power.rolls)


@pytest.mark.parametrize(("tier", "cost", "hp_range", "heal_range"), _JUEMING_TIERS)
@pytest.mark.parametrize(
    ("rolls", "hp", "heal"),
    [
        ({"omen_cost": 9, "hp_pct": 20, "heal_down_pct": 12}, 20, 12),
        ({"omen_cost": 0, "hp_pct": 20, "heal_down_pct": 12}, 20, 12),
        ({"omen_cost": 99, "hp_pct": 20, "heal_down_pct": 12}, 20, 12),
        ({"hp_pct": 20, "heal_down_pct": 12}, 20, 12),
        ({"max_stacks": 6, "damage_pct": 55}, 55, None),
        ({"omen_cost": 1, "execute_pct": 35, "heal_down_pct": 55}, 35, 55),
        ({}, None, None),
    ],
)
def test_jueming_all_loaders_normalize_legacy_and_current_metadata(
    tier, cost, hp_range, heal_range, rolls, hp, heal,
) -> None:
    service = SpiritService(random.Random(42))
    payload = _spirit_payload(tier, "jueming", rolls)
    raw = json.dumps(payload)
    choices_raw = json.dumps([payload])
    artifact = Artifact(
        spirit_json=raw, spirit_pending_json=raw, spirit_choices_json=choices_raw,
    )
    expected = {
        "omen_cost": cost,
        "hp_pct": hp_range[0] if hp is None else max(hp_range[0], min(hp, hp_range[1])),
        "heal_down_pct": heal_range[0] if heal is None else max(heal_range[0], min(heal, heal_range[1])),
    }

    current = service.get_current_spirit(artifact)
    pending = service.get_pending_spirit(artifact)
    choices = service.get_spirit_choices(artifact)
    assert current is not None and pending is not None
    assert len(choices) == 1
    for spirit in (current, pending, choices[0]):
        assert spirit.power.rolls == expected
        assert f"\u2265{cost}" in service._build_spirit_view(artifact, spirit).power_description

    build = PGBuild.from_dict({"spirit_tier": tier, "spirit_power": payload["power"]})
    assert build.spirit_power is not None
    assert build.spirit_power.rolls == expected
    assert PGBuild.from_dict(build.to_dict()).spirit_power == build.spirit_power
    assert payload["power"]["rolls"] == rolls
    assert artifact.spirit_json == raw
    assert artifact.spirit_pending_json == raw
    assert artifact.spirit_choices_json == choices_raw


@pytest.mark.parametrize(("tier", "cost", "hp_range", "heal_range"), _JUEMING_TIERS)
def test_jueming_old_choice_is_normalized_before_pending_and_accept(
    tier, cost, hp_range, heal_range,
) -> None:
    service = SpiritService(random.Random(42))
    payload = _spirit_payload(
        tier, "jueming",
        {"omen_cost": cost + 1, "hp_pct": hp_range[1], "heal_down_pct": heal_range[1]},
    )
    artifact = Artifact(spirit_name="test", spirit_choices_json=json.dumps([payload]))

    picked = service.pick_spirit_choice(artifact, 0)
    assert picked.success and picked.spirit is not None
    assert picked.spirit.power.rolls["omen_cost"] == cost
    assert json.loads(artifact.spirit_pending_json)["power"]["rolls"]["omen_cost"] == cost
    accepted = service.accept_pending_spirit(artifact)
    assert accepted.success and accepted.spirit == picked.spirit
    assert json.loads(artifact.spirit_json)["power"]["rolls"]["omen_cost"] == cost


@pytest.mark.parametrize(("tier", "cost", "hp_range", "heal_range"), _JUEMING_TIERS[:-1])
def test_jueming_owned_tier_upgrade_uses_new_fixed_cost(tier, cost, hp_range, heal_range) -> None:
    service = SpiritService(random.Random(42))
    artifact = Artifact(
        spirit_name="test",
        soul_shards=10_000,
        spirit_json=json.dumps(_spirit_payload(
            tier, "jueming",
            {"omen_cost": cost + 1, "hp_pct": hp_range[1], "heal_down_pct": heal_range[1]},
        )),
    )

    result = service.upgrade_owned_spirit_tier(artifact)

    assert result.success
    assert result.spirit_before is not None and result.spirit_after is not None
    assert result.spirit_before.power.rolls["omen_cost"] == cost
    assert result.tier_after == SPIRIT_TIER_ORDER[SPIRIT_TIER_ORDER.index(tier) + 1]
    assert result.spirit_after.power.rolls["omen_cost"] == cost - 1
    assert service.get_current_spirit(artifact) == result.spirit_after


@pytest.mark.parametrize(("tier", "cost", "hp_range", "heal_range"), _JUEMING_TIERS)
def test_jueming_proving_ground_reroll_and_tier_changes_keep_fixed_cost(
    services, tier, cost, hp_range, heal_range,
) -> None:
    proving_ground = ProvingGroundService(services.combat, random.Random(42))
    build = PGBuild.from_dict({
        "spirit_tier": tier,
        "spirit_power": {
            "power_id": "jueming",
            "rolls": {"omen_cost": cost + 1, "hp_pct": hp_range[0], "heal_down_pct": heal_range[0]},
        },
    })

    _, rerolled = proving_ground.reroll_spirit(build)
    assert rerolled
    assert build.spirit_power is not None
    assert build.spirit_power.rolls["omen_cost"] == cost
    if tier != "supreme":
        _, upgraded = proving_ground.upgrade_spirit_tier(build)
        assert upgraded
        assert build.spirit_power.rolls["omen_cost"] == cost - 1
        run = ProvingGroundRun(character_id=1, pending_affix_ops=0)
        proving_ground._apply_lingshi("accept", build, run, None)
        assert build.spirit_tier == tier
        assert build.spirit_power.rolls["omen_cost"] == cost


def test_lingyong_shared_constants_are_tier_independent() -> None:
    assert LINGYONG_STACK_CAP == 20
    assert LINGYONG_NORMAL_STACK_CAP == 10
    assert LINGYONG_CLEANSE_STACKS_PER_DEBUFF == 2
    assert LINGYONG_TRUE_DAMAGE_PER_STACK_PCT == 2
    assert LINGYONG_TRUE_DAMAGE_ATK_CAP_PCT == 300
    assert (LINGYONG_STACK_CAP - LINGYONG_NORMAL_STACK_CAP) * LINGYONG_TRUE_DAMAGE_PER_STACK_PCT == 20


@pytest.mark.parametrize(("tier", "start_stacks", "per_stack_range"), _LINGYONG_TIERS)
def test_lingyong_original_roll_ranges_and_stored_metadata_are_unchanged(
    tier, start_stacks, per_stack_range,
) -> None:
    definition = get_spirit_power_definition("lingyong")
    assert definition.roll_ranges_by_tier[tier] == (
        ("start_stacks", start_stacks, start_stacks),
        ("per_stack_pct", *per_stack_range),
    )
    for seed in range(20):
        entry = definition.roll(tier, random.Random(seed))
        assert set(entry.rolls) == {"start_stacks", "per_stack_pct"}
        assert entry.rolls["start_stacks"] == start_stacks
        assert per_stack_range[0] <= entry.rolls["per_stack_pct"] <= per_stack_range[1]

    service = SpiritService(random.Random(42))
    for per_stack_pct in per_stack_range:
        rolls = {"start_stacks": start_stacks, "per_stack_pct": per_stack_pct}
        payload = _spirit_payload(tier, "lingyong", rolls)
        raw = json.dumps(payload)
        artifact = Artifact(
            spirit_json=raw, spirit_pending_json=raw, spirit_choices_json=json.dumps([payload]),
        )
        for spirit in (
            service.get_current_spirit(artifact),
            service.get_pending_spirit(artifact),
            *service.get_spirit_choices(artifact),
        ):
            assert spirit is not None and spirit.to_payload() == payload
        build = PGBuild.from_dict({"spirit_tier": tier, "spirit_power": payload["power"]})
        assert build.spirit_power is not None and build.spirit_power.rolls == rolls
        description = definition.describe(rolls)
        assert f" {start_stacks} " in description
        assert f" {per_stack_pct}% " in description
        assert "每满 2 层净化自身 1 层负面" in description
        for shared_value in (" 20 ", " 10 ", " 3 ", " 2%", "20%"):
            assert shared_value in description
        assert len(description) < 400
