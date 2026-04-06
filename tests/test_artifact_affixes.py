from __future__ import annotations

from collections.abc import Sequence

import pytest

from bot.data.artifact_affixes import ArtifactAffixEntry
from bot.ui.artifact import build_artifact_overview_embed, build_refine_panel_embed


class ArtifactRoller:
    def __init__(self, affix_ids: Sequence[str], int_values: Sequence[int], *, fallback_random: float = 0.0) -> None:
        self._affix_ids = iter(affix_ids)
        self._int_values = iter(int_values)
        self._fallback_random = fallback_random

    def choice(self, definitions):
        sample = definitions[0]
        if isinstance(sample, str):
            return sample
        affix_id = next(self._affix_ids)
        for definition in definitions:
            if definition.affix_id == affix_id:
                return definition
        raise AssertionError(f"unknown affix id: {affix_id}")

    def randint(self, start: int, end: int) -> int:
        value = next(self._int_values)
        assert start <= value <= end
        return value

    def random(self) -> float:
        return self._fallback_random


class SequenceRandom:
    def __init__(self, values: Sequence[float], *, fallback: float = 0.99) -> None:
        self._values = iter(values)
        self._fallback = fallback

    def random(self) -> float:
        return next(self._values, self._fallback)


@pytest.mark.asyncio
async def test_affix_slots_unlock_by_reinforce_level(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5001, "灵鉴")).character
        artifact = character.artifact
        assert services.artifact.unlocked_slots(artifact) == 0

        artifact.reinforce_level = 10
        assert services.artifact.unlocked_slots(artifact) == 1

        artifact.reinforce_level = 29
        assert services.artifact.unlocked_slots(artifact) == 2

        artifact.reinforce_level = 30
        assert services.artifact.unlocked_slots(artifact) == 3


@pytest.mark.asyncio
async def test_newly_unlocked_slots_receive_initial_affixes(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun"], [34, 19, 28, 4])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5002, "器灵")).character
        artifact = character.artifact
        artifact.reinforce_level = 30

        newly_unlocked = services.artifact.ensure_affix_slots(artifact)
        await session.commit()

        assert newly_unlocked == (1, 2, 3)
        entries = services.artifact.get_affix_slots(artifact)
        assert [entry.slot for entry in entries] == [1, 2, 3]
        assert [entry.affix_id for entry in entries] == ["huichun", "ningshen", "zhuohun"]


@pytest.mark.asyncio
async def test_refine_pending_persists_and_only_applies_after_save(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 19])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5003, "洗星")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        artifact.soul_shards = 5
        services.artifact.ensure_affix_slots(artifact)

        current_before = services.artifact.get_affix_slots(artifact)[0]
        result = services.artifact.refine_affix(artifact, 1)
        await session.commit()

        assert result.success is True
        assert artifact.soul_shards == 3
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == current_before.affix_id
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "ningshen"

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5003)
        assert character is not None
        artifact = character.artifact
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == "huichun"
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "ningshen"

        save_result = services.artifact.save_pending_affixes(artifact)
        await session.commit()

        assert save_result.success is True

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5003)
        assert character is not None
        artifact = character.artifact
        assert services.artifact.get_affix_slots(artifact)[0].affix_id == "ningshen"
        assert services.artifact.get_pending_affixes(artifact) == []


@pytest.mark.asyncio
async def test_refine_embed_shows_affix_name_and_description(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 19])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5004, "照夜")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        artifact.soul_shards = 4
        services.artifact.ensure_affix_slots(artifact)
        services.artifact.refine_affix(artifact, 1)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        current_field = next(field for field in embed.fields if field.name == "当前词条")
        pending_field = next(field for field in embed.fields if field.name == "待选词条")

        assert "回春" in current_field.value
        assert "最大生命" in current_field.value
        assert "凝神" in pending_field.value
        assert "杀伐提高 19%" in pending_field.value
        field_names = {field.name for field in embed.fields}
        assert "三维加成" not in field_names
        assert "总三维" not in field_names


@pytest.mark.asyncio
async def test_refine_embed_only_shows_refine_related_summary(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5007, "炼词")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        services.artifact.ensure_affix_slots(artifact)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        summary_field = next(field for field in embed.fields if field.name == "洗炼信息")
        assert "器魂" in summary_field.value
        assert "已解锁槽位" in summary_field.value
        assert "待选结果" in summary_field.value


@pytest.mark.asyncio
async def test_artifact_overview_shows_bonus_stats(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5006, "观宝")).character
        artifact = character.artifact
        artifact.atk_bonus = 12
        artifact.def_bonus = 7
        artifact.agi_bonus = 3

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_artifact_overview_embed(snapshot, panel_state)

        bonus_field = next(field for field in embed.fields if field.name == "三维加成")
        assert "+12" in bonus_field.value
        assert "+7" in bonus_field.value
        assert "+3" in bonus_field.value


@pytest.mark.asyncio
async def test_duplicate_affixes_are_allowed(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "huichun"], [18, 42])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5005, "双生")).character
        artifact = character.artifact
        artifact.reinforce_level = 20

        services.artifact.ensure_affix_slots(artifact)
        entries = services.artifact.get_affix_slots(artifact)

        assert [entry.affix_id for entry in entries] == ["huichun", "huichun"]


def test_burn_affix_uses_target_max_hp_percentage(services) -> None:
    attacker = services.combat.create_combatant(
        name="焚心",
        atk=1,
        defense=10,
        agility=100,
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"proc_pct": 100, "burn_pct": 5}),),
    )
    defender = services.combat.create_combatant(name="木人", atk=1, defense=10, agility=1)

    battle = services.combat.run_battle(
        attacker,
        defender,
        rng=SequenceRandom([0.99, 0.99, 0.0]),
    )

    burn_log = next(log for log in battle.logs if log.text and "受灼烧侵蚀" in log.text)
    assert burn_log.target_name == "木人"
    assert burn_log.target_hp_after == 94


def test_ladder_scene_affix_only_applies_in_ladder(services) -> None:
    challenger = services.combat.create_combatant(
        name="先手修士",
        atk=10,
        defense=10,
        agility=90,
        affixes=(ArtifactAffixEntry(1, "zhengheng", {"agi_pct": 28}),),
    )
    defender = services.combat.create_combatant(name="守擂修士", atk=10, defense=10, agility=100)

    ladder_battle = services.combat.run_battle(challenger, defender, scene_tags=("scene_ladder",), rng=SequenceRandom([0.99] * 20))
    neutral_battle = services.combat.run_battle(challenger, defender, scene_tags=("scene_tower",), rng=SequenceRandom([0.99] * 20))

    first_ladder_attack = next(log for log in ladder_battle.logs if log.text is None)
    first_neutral_attack = next(log for log in neutral_battle.logs if log.text is None)

    assert first_ladder_attack.actor_name == "先手修士"
    assert first_neutral_attack.actor_name == "守擂修士"


def test_tower_scene_affix_only_applies_in_tower(services) -> None:
    challenger = services.combat.create_combatant(
        name="登霄修士",
        atk=10,
        defense=10,
        agility=90,
        affixes=(ArtifactAffixEntry(1, "dengxiao", {"atk_pct": 6, "agi_pct": 14}),),
    )
    defender = services.combat.create_combatant(name="守塔修士", atk=10, defense=10, agility=100)

    tower_battle = services.combat.run_battle(challenger, defender, scene_tags=("scene_tower",), rng=SequenceRandom([0.99] * 20))
    ladder_battle = services.combat.run_battle(challenger, defender, scene_tags=("scene_ladder",), rng=SequenceRandom([0.99] * 20))

    first_tower_attack = next(log for log in tower_battle.logs if log.text is None)
    first_ladder_attack = next(log for log in ladder_battle.logs if log.text is None)

    assert first_tower_attack.actor_name == "登霄修士"
    assert first_ladder_attack.actor_name == "守塔修士"
