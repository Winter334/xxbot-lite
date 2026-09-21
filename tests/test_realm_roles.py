from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from functools import total_ordering
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.commands import xian
from bot.data.realms import REALM_BY_KEYS, REALM_STAGES
from bot.models import Base
from bot.utils.time_utils import now_shanghai
from bot.views import panel
from bot.views.realm_roles import claim_realm_role


USER_ID = 24680
GUILD_ID = 13579
REALM_NAMES = {stage.realm_key: stage.realm_name for stage in REALM_STAGES}
IMMORTAL_NAMES = (
    "\u4ed9\u9053",
    "\u5730\u4ed9",
    "\u5929\u4ed9",
    "\u91d1\u4ed9",
)
UNSAFE_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "kick_members",
    "ban_members",
    "moderate_members",
    "manage_webhooks",
    "manage_messages",
    "manage_threads",
    "manage_events",
    "manage_expressions",
    "view_audit_log",
    "mention_everyone",
)


@total_ordering
@dataclass(eq=False, slots=True)
class FakeRole:
    id: int
    name: str
    position: int = 10
    managed: bool = False
    assignable: bool = True
    permissions: discord.Permissions = field(default_factory=discord.Permissions.none)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    def is_default(self) -> bool:
        return self.id == GUILD_ID

    def is_assignable(self) -> bool:
        return (
            self.assignable
            and not self.is_default()
            and not self.managed
            and self.position < 100
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FakeRole):
            return NotImplemented
        return self.id == other.id

    def __lt__(self, other: FakeRole) -> bool:
        return self.position < other.position

    def __hash__(self) -> int:
        return hash(self.id)


class DiscordHarness:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.failures: dict[str, discord.HTTPException] = {}
        self.deferred = False
        self.character = SimpleNamespace(
            realm_key="zhuji",
            stage_key="early",
            realm_index=2,
            stage_index=1,
            highest_floor=0,
            historical_highest_floor=1000,
            reincarnation_count=3,
        )
        self.roles = {
            key: FakeRole(1000 + index, name, position=index + 1)
            for index, (key, name) in enumerate(REALM_NAMES.items())
        }
        self.everyone = FakeRole(GUILD_ID, "@everyone", position=0)
        self.unrelated = FakeRole(2000, "community")
        self.immortals = [
            FakeRole(3000 + index, name)
            for index, name in enumerate(IMMORTAL_NAMES)
        ]
        self.guild_roles = [
            self.everyone,
            *self.roles.values(),
            self.unrelated,
            *self.immortals,
        ]
        self.member = SimpleNamespace(
            id=USER_ID,
            roles=[self.everyone, self.unrelated],
            add_roles=AsyncMock(side_effect=self._add_roles),
            remove_roles=AsyncMock(side_effect=self._remove_roles),
            edit=AsyncMock(side_effect=AssertionError("Do not replace all member roles")),
        )
        self.guild = SimpleNamespace(
            id=GUILD_ID,
            roles=[],
            me=SimpleNamespace(
                guild_permissions=discord.Permissions(manage_roles=True),
                top_role=FakeRole(9000, "bot", position=100),
            ),
            fetch_roles=AsyncMock(side_effect=self._fetch_roles),
            fetch_member=AsyncMock(side_effect=self._fetch_member),
        )
        self.session = AsyncMock(spec=AsyncSession)
        self.session.__aenter__.return_value = self.session
        self.session.commit.side_effect = AssertionError("Claims must not commit game state")
        self.bot = SimpleNamespace(
            settings=SimpleNamespace(realm_role_ids={}, realm_role_cleanup_ids=frozenset()),
            session_factory=Mock(return_value=self.session),
            character_service=SimpleNamespace(
                get_character_by_discord_id=AsyncMock(side_effect=self._lookup),
                get_or_create_character=AsyncMock(
                    side_effect=AssertionError("Claims must not create characters")
                ),
                refresh_combat_power=Mock(
                    side_effect=AssertionError("Claims must not refresh game state")
                ),
            ),
        )
        self.new_interaction()

    def new_interaction(self) -> None:
        self.deferred = False
        self.events.clear()
        self.interaction = SimpleNamespace(
            guild=self.guild,
            user=SimpleNamespace(id=USER_ID, display_name="cultivator", roles=[]),
            client=self.bot,
            response=SimpleNamespace(
                defer=AsyncMock(side_effect=self._defer),
                send_message=AsyncMock(),
                is_done=Mock(side_effect=lambda: self.deferred),
            ),
            followup=SimpleNamespace(send=AsyncMock(side_effect=self._followup)),
        )

    async def _defer(self, *, ephemeral: bool, thinking: bool) -> None:
        assert ephemeral is True
        assert thinking is True
        assert not self.deferred
        self.deferred = True
        self.events.append("defer")

    def _network(self, operation: str) -> None:
        assert self.deferred, f"{operation} happened before defer"
        self.events.append(operation)
        if operation in self.failures:
            raise self.failures[operation]

    async def _lookup(self, session, user_id):
        assert self.deferred
        assert session is self.session
        assert user_id == USER_ID
        self.events.append("lookup")
        return self.character

    async def _fetch_roles(self) -> list[FakeRole]:
        self._network("fetch_roles")
        return list(self.guild_roles)

    async def _fetch_member(self, user_id: int):
        self._network("fetch_member")
        assert user_id == USER_ID
        return self.member

    async def _add_roles(self, *roles, reason: str, atomic: bool) -> None:
        self._network("add_roles")
        assert reason and isinstance(reason, str)
        assert atomic is True
        for role in roles:
            if role not in self.member.roles:
                self.member.roles.append(role)

    async def _remove_roles(self, *roles, reason: str, atomic: bool) -> None:
        self._network("remove_roles")
        assert reason and isinstance(reason, str)
        assert atomic is True
        removed_ids = {role.id for role in roles}
        self.member.roles[:] = [
            role for role in self.member.roles if role.id not in removed_ids
        ]

    async def _followup(self, *args, **kwargs) -> None:
        self._network("followup")


@pytest.fixture
def harness() -> DiscordHarness:
    return DiscordHarness()


def _message_content(call) -> str:
    content = call.args[0] if call.args else call.kwargs.get("content")
    assert isinstance(content, str) and content.strip()
    return content


def _assert_private_followup(harness: DiscordHarness) -> str:
    response = harness.interaction.response
    response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    response.send_message.assert_not_called()
    followup = harness.interaction.followup.send
    followup.assert_awaited_once()
    call = followup.await_args
    assert call.kwargs["ephemeral"] is True
    assert call.kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert harness.events[0] == "defer"
    assert harness.events[-1] == "followup"
    harness.member.edit.assert_not_called()
    harness.bot.character_service.get_or_create_character.assert_not_called()
    harness.bot.character_service.refresh_combat_power.assert_not_called()
    harness.session.commit.assert_not_called()
    return _message_content(call)


def _assert_no_role_writes(harness: DiscordHarness) -> None:
    harness.member.add_roles.assert_not_called()
    harness.member.remove_roles.assert_not_called()
    harness.member.edit.assert_not_called()


def _assert_incomplete(content: str) -> None:
    assert "\u5df2\u9886\u53d6" not in content
    assert any(
        text in content
        for text in (
            "\u672a",
            "\u65e0\u6cd5",
            "\u4e0d\u80fd",
            "\u5931\u8d25",
            "\u7ba1\u7406\u5458",
            "\u91cd\u8bd5",
        )
    )


def _http_error(error_type: type[discord.HTTPException]) -> discord.HTTPException:
    status = {
        discord.HTTPException: 503,
        discord.Forbidden: 403,
        discord.NotFound: 404,
    }[error_type]
    response = SimpleNamespace(status=status, reason="Discord boundary failure")
    return error_type(response, {"code": 0, "message": "simulated API failure"})


async def test_dm_is_private_and_does_not_query_or_call_discord(harness) -> None:
    harness.interaction.guild = None

    await claim_realm_role(harness.bot, harness.interaction)

    response = harness.interaction.response
    response.send_message.assert_awaited_once()
    assert response.send_message.await_args.kwargs["ephemeral"] is True
    assert "\u670d\u52a1\u5668" in _message_content(response.send_message.await_args)
    response.defer.assert_not_called()
    harness.interaction.followup.send.assert_not_called()
    harness.bot.session_factory.assert_not_called()
    harness.bot.character_service.get_character_by_discord_id.assert_not_called()
    harness.bot.character_service.get_or_create_character.assert_not_called()
    harness.guild.fetch_roles.assert_not_called()
    harness.guild.fetch_member.assert_not_called()
    _assert_no_role_writes(harness)


async def test_unregistered_user_is_told_to_create_character_without_mutation(harness) -> None:
    harness.character = None

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert "/\u4fee\u4ed9" in content
    harness.bot.character_service.get_character_by_discord_id.assert_awaited_once_with(
        harness.session, USER_ID
    )
    _assert_no_role_writes(harness)


@pytest.mark.parametrize(
    "stage",
    REALM_STAGES,
    ids=[f"{stage.realm_key}-{stage.stage_key}" for stage in REALM_STAGES],
)
@pytest.mark.parametrize("with_stage_role", [False, True], ids=["major-only", "stage-decoy"])
async def test_all_open_realms_and_stages_grant_only_current_major_realm(
    harness, stage, with_stage_role
) -> None:
    harness.character.realm_key = stage.realm_key
    harness.character.stage_key = stage.stage_key
    harness.character.realm_index = stage.realm_index
    harness.character.stage_index = stage.stage_index
    if with_stage_role:
        harness.guild_roles.append(FakeRole(4000, stage.display_name))
    target = harness.roles[stage.realm_key]

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert stage.realm_name in content
    harness.guild.fetch_roles.assert_awaited_once_with()
    harness.guild.fetch_member.assert_awaited_once_with(USER_ID)
    assert harness.member.add_roles.await_args.args == (target,)
    assert harness.member.add_roles.await_count == 1
    harness.member.remove_roles.assert_not_called()
    assert set(harness.member.roles) == {harness.everyone, harness.unrelated, target}
    assert harness.events == [
        "defer", "lookup", "fetch_roles", "fetch_member", "add_roles", "followup"
    ]


@pytest.mark.parametrize(
    ("realm_key", "stage_key"),
    [
        ("xiandao", "early"),
        ("dixian", "early"),
        ("tianxian", "early"),
        ("lianqi", "unknown"),
        ("weixian", "ascended"),
        ("unknown", "early"),
        (None, "early"),
        ("zhuji", None),
    ],
)
async def test_unopened_realm_or_stage_is_rejected_even_with_configured_id(
    harness, realm_key, stage_key
) -> None:
    assert (realm_key, stage_key) not in REALM_BY_KEYS
    harness.character.realm_key = realm_key
    harness.character.stage_key = stage_key
    harness.bot.settings.realm_role_ids = {realm_key: harness.immortals[0].id}
    harness.bot.settings.realm_role_cleanup_ids = frozenset(role.id for role in harness.immortals)
    harness.member.roles.extend(harness.immortals)

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert "\u672a\u5f00\u653e" in content
    _assert_no_role_writes(harness)


async def test_current_keys_override_historical_floor_and_stale_indices(harness) -> None:
    harness.character.realm_key = "lianqi"
    harness.character.stage_key = "mid"
    harness.character.realm_index = 10
    harness.character.stage_index = 4
    harness.member.roles.append(harness.roles["weixian"])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (harness.roles["lianqi"],)
    assert harness.member.remove_roles.await_args.args == (harness.roles["weixian"],)


async def test_replaces_all_old_realms_after_add_and_preserves_unrelated_and_immortal_roles(
    harness,
) -> None:
    old_roles = [role for key, role in harness.roles.items() if key != "zhuji"]
    harness.member.roles.extend([*old_roles, *harness.immortals])
    target = harness.roles["zhuji"]

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (target,)
    assert set(harness.member.remove_roles.await_args.args) == set(old_roles)
    assert harness.member.remove_roles.await_count == 1
    assert harness.events.index("add_roles") < harness.events.index("remove_roles")
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, target, *harness.immortals
    }


@pytest.mark.parametrize("has_target", [False, True], ids=["new-target", "existing-target"])
async def test_cleanup_removes_only_selected_legacy_roles_once_and_preserves_target(
    harness, has_target
) -> None:
    target = harness.roles["zhuji"]
    old_roles = [harness.roles["lianqi"], harness.roles["jiedan"]]
    decorated = FakeRole(5000, f"[legacy] {REALM_NAMES['jiedan']}")
    unconfigured = FakeRole(5001, decorated.name)
    harness.guild_roles.extend([decorated, unconfigured])
    harness.member.roles.extend([*old_roles, decorated, unconfigured, *harness.immortals])
    if has_target:
        harness.member.roles.append(target)
    harness.bot.settings.realm_role_ids = {"lianqi": old_roles[0].id}
    legacy_roles = [decorated, harness.immortals[0], harness.immortals[-1]]
    harness.bot.settings.realm_role_cleanup_ids = frozenset(
        [target.id, old_roles[0].id, *(role.id for role in legacy_roles)]
    )

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    removed = harness.member.remove_roles.await_args.args
    assert set(removed) == {*old_roles, *legacy_roles}
    assert len(removed) == len(set(removed))
    assert harness.member.remove_roles.await_count == 1
    if has_target:
        harness.member.add_roles.assert_not_called()
    else:
        assert harness.member.add_roles.await_count == 1
        assert harness.member.add_roles.await_args.args == (target,)
        assert harness.events.index("add_roles") < harness.events.index("remove_roles")
    expected = {
        harness.everyone, harness.unrelated, target, unconfigured, *harness.immortals[1:-1]
    }
    assert set(harness.member.roles) == expected

    harness.member.add_roles.reset_mock()
    harness.member.remove_roles.reset_mock()
    harness.new_interaction()
    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    _assert_no_role_writes(harness)
    assert set(harness.member.roles) == expected


async def test_cleanup_ignores_missing_other_guild_and_unheld_ids(harness) -> None:
    deleted = FakeRole(7000, "deleted legacy", permissions=discord.Permissions(administrator=True))
    other_guild = FakeRole(7001, "other guild legacy", managed=True)
    harness.guild.roles = [*harness.guild_roles, other_guild]
    harness.member.roles.append(deleted)
    unheld = harness.immortals[0]
    unheld.permissions = discord.Permissions(administrator=True)
    harness.bot.settings.realm_role_cleanup_ids = frozenset(
        {deleted.id, other_guild.id, unheld.id, 999999}
    )
    original_roles = set(harness.member.roles)
    target = harness.roles["zhuji"]

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_count == 1
    assert harness.member.add_roles.await_args.args == (target,)
    harness.member.remove_roles.assert_not_called()
    assert set(harness.member.roles) == original_roles | {target}


async def test_unrelated_role_added_concurrently_is_not_overwritten(harness) -> None:
    old = harness.roles["lianqi"]
    concurrent_role = FakeRole(8000, "concurrent award")
    harness.member.roles.append(old)

    async def add_then_external_award(*roles, **kwargs):
        await harness._add_roles(*roles, **kwargs)
        harness.member.roles.append(concurrent_role)

    harness.member.add_roles.side_effect = add_then_external_award

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, concurrent_role, harness.roles["zhuji"]
    }


async def test_repeat_claim_has_no_role_api_writes(harness) -> None:
    target = harness.roles["zhuji"]
    harness.member.roles.extend([target, *harness.immortals])

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert target.name in content
    assert any(text in content for text in ("\u5df2", "\u91cd\u590d", "\u65e0\u9700"))
    _assert_no_role_writes(harness)


async def test_repeat_claim_still_cleans_old_realms_without_readding_target(harness) -> None:
    target = harness.roles["zhuji"]
    old = harness.roles["lianqi"]
    harness.member.roles.extend([target, old, *harness.immortals])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    harness.member.add_roles.assert_not_called()
    assert harness.member.remove_roles.await_args.args == (old,)
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, target, *harness.immortals
    }


async def test_fetched_member_roles_are_authoritative_over_interaction_cache(harness) -> None:
    target = harness.roles["zhuji"]
    harness.interaction.user.roles = [harness.everyone, target]
    harness.member.roles.append(harness.roles["lianqi"])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    harness.guild.fetch_member.assert_awaited_once_with(USER_ID)
    assert harness.member.add_roles.await_args.args == (target,)
    assert harness.member.remove_roles.await_args.args == (harness.roles["lianqi"],)


@pytest.mark.parametrize("variant", ["missing", "prefix", "suffix", "stage", "whitespace"])
async def test_target_requires_exact_name_and_missing_target_never_removes_old(harness, variant) -> None:
    target = harness.roles["zhuji"]
    harness.guild_roles.remove(target)
    variants = {
        "prefix": f"VIP {target.name}",
        "suffix": f"{target.name} VIP",
        "stage": REALM_BY_KEYS[("zhuji", "early")].display_name,
        "whitespace": f" {target.name} ",
    }
    legacy = harness.immortals[0]
    cleanup_ids = {legacy.id}
    if variant != "missing":
        decoy = FakeRole(5000, variants[variant])
        harness.guild_roles.append(decoy)
        cleanup_ids.add(decoy.id)
    harness.bot.settings.realm_role_cleanup_ids = frozenset(cleanup_ids)
    harness.member.roles.extend([harness.roles["lianqi"], legacy])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


@pytest.mark.parametrize("duplicate_key", ["zhuji", "lianqi", "weixian"])
async def test_any_ambiguous_open_realm_name_stops_before_writes(harness, duplicate_key) -> None:
    harness.guild_roles.append(FakeRole(5000, REALM_NAMES[duplicate_key]))

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert REALM_NAMES[duplicate_key] in content
    assert any(text in content for text in ("\u591a\u4e2a", "\u91cd\u590d", "\u552f\u4e00"))
    _assert_no_role_writes(harness)


async def test_duplicate_unrelated_or_unopened_names_do_not_block_claim(harness) -> None:
    harness.guild_roles.extend([
        FakeRole(5000, harness.unrelated.name),
        FakeRole(5001, IMMORTAL_NAMES[0]),
    ])
    harness.member.roles.extend(harness.immortals)

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (harness.roles["zhuji"],)
    assert all(role in harness.member.roles for role in harness.immortals)


async def test_id_override_wins_over_duplicate_names_and_preserves_unselected_roles(harness) -> None:
    default = harness.roles["zhuji"]
    duplicate = FakeRole(5000, default.name)
    target = FakeRole(5001, "custom realm @everyone <@&12345>")
    harness.guild_roles.extend([duplicate, target])
    harness.member.roles.extend([default, duplicate, harness.roles["lianqi"]])
    harness.bot.settings.realm_role_ids = {"zhuji": target.id}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (target,)
    assert harness.member.remove_roles.await_args.args == (harness.roles["lianqi"],)
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, target, default, duplicate
    }


async def test_missing_configured_target_id_does_not_fall_back_to_name(harness) -> None:
    harness.bot.settings.realm_role_ids = {"zhuji": 999999}
    harness.member.roles.append(harness.roles["lianqi"])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


async def test_missing_configured_old_realm_id_does_not_delete_same_name_role(harness) -> None:
    old_default = harness.roles["lianqi"]
    harness.member.roles.append(old_default)
    harness.bot.settings.realm_role_ids = {"lianqi": 999999}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (harness.roles["zhuji"],)
    harness.member.remove_roles.assert_not_called()
    assert old_default in harness.member.roles


async def test_id_override_resolves_renamed_old_role_without_deleting_name_fallback(harness) -> None:
    old_default = harness.roles["lianqi"]
    old_configured = FakeRole(5000, "old configured realm")
    harness.guild_roles.append(old_configured)
    harness.member.roles.extend([old_default, old_configured])
    harness.bot.settings.realm_role_ids = {"lianqi": old_configured.id}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.remove_roles.await_args.args == (old_configured,)
    assert old_default in harness.member.roles


async def test_configured_id_is_excluded_from_other_realms_name_candidates(harness) -> None:
    target = harness.roles["lianqi"]
    old = FakeRole(5000, target.name)
    harness.guild_roles.append(old)
    harness.member.roles.extend([old, harness.roles["zhuji"]])
    harness.bot.settings.realm_role_ids = {"zhuji": target.id}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (target,)
    assert harness.member.remove_roles.await_args.args == (old,)
    assert harness.roles["zhuji"] in harness.member.roles
    assert target in harness.member.roles


async def test_role_reserved_by_another_realm_cannot_be_name_fallback_target(harness) -> None:
    harness.bot.settings.realm_role_ids = {"lianqi": harness.roles["zhuji"].id}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


async def test_duplicate_id_bindings_are_rejected_without_writes(harness) -> None:
    target = harness.roles["zhuji"]
    harness.bot.settings.realm_role_ids = {"lianqi": target.id, "zhuji": target.id}

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


@pytest.mark.parametrize("missing_bot_member", [False, True], ids=["no-manage-roles", "no-bot-member"])
async def test_bot_must_have_manage_roles_permission(harness, missing_bot_member) -> None:
    if missing_bot_member:
        harness.guild.me = None
    else:
        harness.guild.me.guild_permissions = discord.Permissions.none()
    harness.member.roles.append(harness.roles["lianqi"])

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


@pytest.mark.parametrize("role_kind", ["target", "old", "cleanup"])
@pytest.mark.parametrize(
    "problem",
    ["everyone", "managed", "unassignable", "equal-position", "higher-position", *UNSAFE_PERMISSIONS],
)
async def test_all_roles_are_preflighted_before_any_write(harness, role_kind, problem) -> None:
    key = "zhuji" if role_kind == "target" else "jiedan"
    role = harness.immortals[0] if role_kind == "cleanup" else harness.roles[key]
    safe_cleanup = harness.immortals[-1]
    harness.member.roles.extend([harness.roles["lianqi"], safe_cleanup])
    harness.bot.settings.realm_role_cleanup_ids = frozenset({safe_cleanup.id})
    if role_kind != "target":
        harness.member.roles.append(role)
    if role_kind == "cleanup":
        harness.bot.settings.realm_role_cleanup_ids |= {role.id}

    if problem == "everyone":
        if role_kind == "cleanup":
            harness.bot.settings.realm_role_cleanup_ids |= {harness.everyone.id}
        else:
            harness.bot.settings.realm_role_ids = {key: harness.everyone.id}
    elif problem == "managed":
        role.managed = True
    elif problem == "unassignable":
        role.assignable = False
    elif problem in ("equal-position", "higher-position"):
        role.position = 100 if problem == "equal-position" else 101
    else:
        role.permissions = discord.Permissions(**{problem: True})

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


@pytest.mark.parametrize("role_kind", ["target", "old", "cleanup"])
async def test_fetched_role_permissions_override_stale_member_role_objects(harness, role_kind) -> None:
    if role_kind == "cleanup":
        role = harness.immortals[0]
        harness.bot.settings.realm_role_cleanup_ids = frozenset({role.id})
    else:
        role = harness.roles["zhuji" if role_kind == "target" else "lianqi"]
    harness.member.roles.append(FakeRole(role.id, "stale cached name"))
    role.permissions = discord.Permissions(administrator=True)

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_incomplete(_assert_private_followup(harness))
    _assert_no_role_writes(harness)


async def test_cleanup_uses_fetched_safe_role_instead_of_stale_unsafe_member_role(harness) -> None:
    role = harness.immortals[0]
    stale = FakeRole(role.id, "stale legacy", permissions=discord.Permissions(administrator=True))
    harness.member.roles.append(stale)
    harness.bot.settings.realm_role_cleanup_ids = frozenset({role.id})

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (harness.roles["zhuji"],)
    harness.member.remove_roles.assert_awaited_once()
    assert harness.member.remove_roles.await_args.args[0] is role
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, harness.roles["zhuji"]
    }


async def test_unsafe_unheld_realms_do_not_block_safe_claim(harness) -> None:
    harness.roles["weixian"].permissions = discord.Permissions(administrator=True)
    harness.unrelated.permissions = discord.Permissions(manage_messages=True)
    harness.member.roles.extend(harness.immortals)

    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_args.args == (harness.roles["zhuji"],)
    harness.member.remove_roles.assert_not_called()
    assert harness.unrelated in harness.member.roles


@pytest.mark.parametrize(
    "error_type", [discord.HTTPException, discord.Forbidden, discord.NotFound],
    ids=["http", "forbidden", "not-found"],
)
@pytest.mark.parametrize("operation", ["fetch_roles", "fetch_member", "add_roles"])
async def test_discord_failures_are_private_friendly_and_do_not_remove_old_roles(
    harness, operation, error_type
) -> None:
    old = harness.roles["lianqi"]
    legacy = harness.immortals[0]
    harness.member.roles.extend([old, legacy])
    harness.bot.settings.realm_role_cleanup_ids = frozenset({legacy.id})
    original_roles = list(harness.member.roles)
    harness.failures[operation] = _http_error(error_type)

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    _assert_incomplete(content)
    assert "simulated API failure" not in content
    assert harness.member.roles == original_roles
    harness.member.remove_roles.assert_not_called()
    if operation != "add_roles":
        harness.member.add_roles.assert_not_called()
    else:
        harness.member.add_roles.assert_awaited_once()


@pytest.mark.parametrize(
    "error_type", [discord.HTTPException, discord.Forbidden, discord.NotFound],
    ids=["http", "forbidden", "not-found"],
)
@pytest.mark.parametrize("partial_removal", [False, True], ids=["none-removed", "one-removed"])
async def test_old_role_removal_failure_reports_partial_success_and_retry_finishes(
    harness, error_type, partial_removal
) -> None:
    target = harness.roles["zhuji"]
    legacy = harness.immortals[0]
    old_roles = [harness.roles["lianqi"], harness.roles["jiedan"], legacy]
    harness.member.roles.extend([*old_roles, *harness.immortals[1:]])
    harness.bot.settings.realm_role_cleanup_ids = frozenset({legacy.id})

    async def fail_removal(*roles, reason, atomic):
        harness._network("remove_roles")
        assert reason
        assert atomic is True
        assert set(roles) == set(old_roles)
        if partial_removal:
            harness.member.roles.remove(legacy)
        raise _http_error(error_type)

    harness.member.remove_roles.side_effect = fail_removal

    await claim_realm_role(harness.bot, harness.interaction)

    content = _assert_private_followup(harness)
    assert target.name in content
    assert "\u65e7" in content
    assert "\u518d" in content or "\u91cd\u8bd5" in content
    assert "\u672a" in content or "\u5931\u8d25" in content
    assert harness.events.index("add_roles") < harness.events.index("remove_roles")
    assert target in harness.member.roles
    expected_remaining = set(old_roles) - ({legacy} if partial_removal else set())
    assert expected_remaining.issubset(harness.member.roles)

    harness.member.remove_roles.side_effect = harness._remove_roles
    harness.new_interaction()
    await claim_realm_role(harness.bot, harness.interaction)

    _assert_private_followup(harness)
    assert harness.member.add_roles.await_count == 1
    assert harness.member.remove_roles.await_count == 2
    assert set(harness.member.remove_roles.await_args.args) == expected_remaining
    assert set(harness.member.roles) == {
        harness.everyone, harness.unrelated, target, *harness.immortals[1:]
    }


async def _database_snapshot(session_factory) -> dict[str, list[tuple]]:
    async with session_factory() as session:
        return {
            table.name: [
                tuple(row)
                for row in (await session.execute(select(table).order_by(*table.primary_key))).all()
            ]
            for table in Base.metadata.tables.values()
        }


@pytest.mark.parametrize("registered", [False, True], ids=["unregistered", "existing-character"])
async def test_real_database_claim_is_read_only_and_does_not_advance_game(
    harness, session_factory, services, registered
) -> None:
    if registered:
        async with session_factory() as session:
            creation = await services.character.get_or_create_character(session, USER_ID, "cultivator")
            character = creation.character
            character.realm_key = "lianqi"
            character.stage_key = "late"
            character.stage_index = 3
            character.historical_highest_floor = 1000
            character.reincarnation_count = 3
            character.cultivation = 27
            character.current_qi = 0
            character.is_retreating = True
            character.is_traveling = True
            character.last_idle_at = now_shanghai() - timedelta(days=2)
            character.last_qi_recovered_at = character.last_idle_at
            character.travel_started_at = character.last_idle_at
            await session.commit()
        harness.member.roles.append(harness.roles["weixian"])

    before = await _database_snapshot(session_factory)
    harness.bot.session_factory = session_factory
    lookup = AsyncMock(wraps=services.character.get_character_by_discord_id)
    harness.bot.character_service.get_character_by_discord_id = lookup
    engine = session_factory.kw["bind"]
    statements = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        await claim_realm_role(harness.bot, harness.interaction)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)

    content = _assert_private_followup(harness)
    lookup.assert_awaited_once()
    assert lookup.await_args.args[1] == USER_ID
    assert statements
    assert all(
        statement.lstrip().split(maxsplit=1)[0].upper() not in {"INSERT", "UPDATE", "DELETE", "REPLACE"}
        for statement in statements
    )
    assert await _database_snapshot(session_factory) == before
    if registered:
        assert harness.member.add_roles.await_args.args == (harness.roles["lianqi"],)
        assert harness.member.remove_roles.await_args.args == (harness.roles["weixian"],)
    else:
        assert "/\u4fee\u4ed9" in content
        _assert_no_role_writes(harness)


async def test_slash_command_delegates_to_shared_claim_without_other_work(harness, monkeypatch) -> None:
    shared_claim = AsyncMock()
    monkeypatch.setattr(xian, "claim_realm_role", shared_claim)
    command = xian.XianCommands(harness.bot)
    command._ensure_npc_pool = AsyncMock()
    assert command.claim_realm_role.guild_only is True

    await command.claim_realm_role.callback(command, harness.interaction)

    shared_claim.assert_awaited_once_with(harness.bot, harness.interaction)
    command._ensure_npc_pool.assert_not_called()
    harness.bot.session_factory.assert_not_called()
    harness.interaction.response.defer.assert_not_called()
    harness.interaction.response.send_message.assert_not_called()
    harness.interaction.followup.send.assert_not_called()


async def test_panel_button_delegates_to_shared_claim(harness, monkeypatch) -> None:
    shared_claim = AsyncMock()
    monkeypatch.setattr(panel, "claim_realm_role", shared_claim)
    view = panel.PanelView(USER_ID)
    try:
        assert await view.interaction_check(harness.interaction) is True
        await view.realm_role_button.callback(harness.interaction)
    finally:
        view.stop()

    shared_claim.assert_awaited_once_with(harness.bot, harness.interaction)
    harness.bot.session_factory.assert_not_called()
    harness.interaction.response.defer.assert_not_called()
    harness.interaction.response.send_message.assert_not_called()
    harness.interaction.followup.send.assert_not_called()


async def test_panel_owner_lock_rejects_other_users_privately(harness, monkeypatch) -> None:
    shared_claim = AsyncMock()
    monkeypatch.setattr(panel, "claim_realm_role", shared_claim)
    harness.interaction.user.id = USER_ID + 1
    view = panel.PanelView(USER_ID)
    try:
        assert view.realm_role_button in view.children
        assert await view.interaction_check(harness.interaction) is False
    finally:
        view.stop()

    shared_claim.assert_not_called()
    harness.interaction.response.send_message.assert_awaited_once()
    assert harness.interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    harness.bot.session_factory.assert_not_called()
    _assert_no_role_writes(harness)


async def test_panel_with_realm_role_button_stays_within_discord_component_limits() -> None:
    view = panel.PanelView(USER_ID)
    try:
        assert isinstance(view.realm_role_button, discord.ui.Button)
        assert view.realm_role_button in view.children
        assert view.realm_role_button.disabled is False
        assert len(view.children) <= 25
        rows = view.to_components()
        assert len(rows) <= 5
        for row in rows:
            components = row["components"]
            assert 1 <= len(components) <= 5
            assert all(component["type"] == discord.ComponentType.button.value for component in components)
            assert all(len(component.get("label", "")) <= 80 for component in components)
        custom_ids = [child.custom_id for child in view.children]
        assert len(custom_ids) == len(set(custom_ids))
    finally:
        view.stop()
