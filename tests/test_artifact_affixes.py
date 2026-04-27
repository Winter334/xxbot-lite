from __future__ import annotations

from collections.abc import Sequence
import json

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
        value = next(self._int_values, start)
        return max(start, min(value, end))

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
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun"], [34, 8, 50, 30, 3, 4])
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
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 8, 50])
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
async def test_discard_pending_affix_only_removes_selected_slot(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen", "zhuohun", "lueying"], [34, 8, 50, 30, 3, 4, 50, 30, 10])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5008, "弃词")).character
        artifact = character.artifact
        artifact.reinforce_level = 20
        artifact.soul_shards = 8
        services.artifact.ensure_affix_slots(artifact)
        services.artifact.refine_affix(artifact, 1)
        services.artifact.refine_affix(artifact, 2)

        discard_result = services.artifact.discard_pending_affix(artifact, 2)
        await session.commit()

        assert discard_result.success is True
        assert discard_result.discarded_entry is not None
        assert discard_result.discarded_entry.affix_id == "lueying"
        assert [entry.slot for entry in services.artifact.get_pending_affixes(artifact)] == [1]
        assert services.artifact.get_pending_affixes(artifact)[0].affix_id == "zhuohun"

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5008)
        assert character is not None
        artifact = character.artifact
        assert [entry.slot for entry in services.artifact.get_pending_affixes(artifact)] == [1]

        save_result = services.artifact.save_pending_affixes(artifact)
        await session.commit()

        assert save_result.success is True
        assert save_result.applied_slots == (1,)

    async with session_factory() as session:
        character = await services.character.get_character_by_discord_id(session, 5008)
        assert character is not None
        artifact = character.artifact
        assert [entry.affix_id for entry in services.artifact.get_affix_slots(artifact)] == ["zhuohun", "ningshen"]
        assert services.artifact.get_pending_affixes(artifact) == []


@pytest.mark.asyncio
async def test_discard_pending_affix_fails_cleanly_without_pending(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun"], [34, 8])
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5009, "空弃")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        services.artifact.ensure_affix_slots(artifact)

        result = services.artifact.discard_pending_affix(artifact, 1)

        assert result.success is False
        assert result.discarded_entry is None
        assert result.message == "槽1 当前没有可放弃的待选词条。"


@pytest.mark.asyncio
async def test_refine_embed_shows_affix_name_and_description(session_factory, services) -> None:
    services.artifact.rng = ArtifactRoller(["huichun", "ningshen"], [34, 8, 50])
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
        assert "灵势" in pending_field.value
        field_names = {field.name for field in embed.fields}
        assert "三维加成" not in field_names
        assert "总三维" not in field_names


@pytest.mark.asyncio
async def test_affix_panel_describes_legacy_rolls_with_new_defaults(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5011, "旧词条")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        artifact.affix_slots_json = json.dumps(
            [
                {
                    "slot": 1,
                    "affix_id": "zhuohun",
                    "rolls": {"proc_pct": 25, "burn_pct": 2},
                }
            ]
        )

        panel_state = services.artifact.build_panel_state(artifact)
        current_slot = panel_state.current_slots[0]

        assert current_slot.name == "灼魂"
        assert "灼痕" in current_slot.description
        assert "4%" in current_slot.description


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
async def test_refine_embed_footer_mentions_single_slot_discard(session_factory, services) -> None:
    async with session_factory() as session:
        character = (await services.character.get_or_create_character(session, 5010, "弃槽提示")).character
        artifact = character.artifact
        artifact.reinforce_level = 10
        services.artifact.ensure_affix_slots(artifact)

        snapshot = services.character.build_snapshot(character)
        panel_state = services.artifact.build_panel_state(artifact)
        embed = build_refine_panel_embed(snapshot, panel_state)

        assert embed.footer.text is not None
        assert "弃槽X" in embed.footer.text


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
    services.artifact.rng = ArtifactRoller(["huichun", "huichun"], [25, 55, 8, 16])
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
        affixes=(ArtifactAffixEntry(1, "zhuohun", {"proc_pct": 100, "burn_pct": 5, "scar_bonus_pct": 0}),),
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


def test_lueying_creates_fast_attack_agility_gap(services) -> None:
    challenger = services.combat.create_combatant(
        name="掠影修士",
        atk=10,
        defense=10,
        agility=80,
        affixes=(ArtifactAffixEntry(1, "lueying", {"agi_pct": 80, "proc_pct": 0, "agi_down_pct": 8}),),
    )
    defender = services.combat.create_combatant(name="守擂修士", atk=10, defense=10, agility=100)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 20))
    first_attack = next(log for log in battle.logs if log.text is None)

    assert first_attack.actor_name == "掠影修士"


def test_dengxiao_scales_as_late_game_affix(services) -> None:
    challenger = services.combat.create_combatant(
        name="登霄修士",
        atk=10,
        defense=40,
        agility=100,
        affixes=(ArtifactAffixEntry(1, "dengxiao", {"damage_pct": 9, "pierce_pct": 7}),),
    )
    defender = services.combat.create_combatant(name="守塔修士", atk=1, defense=40, agility=1)

    battle = services.combat.run_battle(challenger, defender, rng=SequenceRandom([0.99] * 80))

    assert any(log.text and "登霄势涨" in log.text for log in battle.logs)
