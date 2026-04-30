from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import random
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.realms import get_stage
from bot.data.resource_sites import SITE_NAME_POOLS, SITE_TYPE_NAMES, SITE_TYPE_WEIGHTS
from bot.models.character import Character
from bot.models.sect import Sect
from bot.models.world_resource_site import WorldResourceSite
from bot.utils.time_utils import ensure_shanghai, now_shanghai, today_shanghai

FACTION_NAMES = {
    "neutral": "中立",
    "righteous": "正道",
    "demonic": "魔道",
}

SECT_MEMBER_LIMIT = 30
SECT_JOIN_COOLDOWN_HOURS = 24
SECT_JOIN_REWARD_LOCK_HOURS = 24
SECT_ACTION_QI_COST = 1
SITE_LIFETIME_DAYS = 3
DAILY_SITE_SPAWN_COUNT = 2
LEGACY_SITE_TYPE_FALLBACK = "lingshi"
UNOWNED_SITE_PROGRESS_TARGET = 2
OWNED_SITE_PROGRESS_TARGET = 4
DIRECT_WIN_RATIO = 1.30
DIRECT_LOSS_RATIO = 0.70
SWING_WIN_RATE_MIN = 0.35
SWING_WIN_RATE_MAX = 0.65
SITE_ACTION_LABELS = {
    "contest": "强争",
    "guard": "护持",
    "transport": "输运",
}
SITE_ROLE_NAMES = {
    "contest": "强争",
    "guard": "护持",
    "transport": "输运",
}
SITE_REWARD_CONFIG = {
    "lingshi": {"lingshi": 400, "soul": 20},
    "soul": {"lingshi": 240, "soul": 36},
}
TASK_STATUS_NAMES = {
    "available": "未领取",
    "active": "进行中",
    "completed": "可领奖",
    "claimed": "已领取",
}


@dataclass(frozen=True, slots=True)
class SectTaskDefinition:
    key: str
    title: str
    description: str
    target: int
    reward_lingshi: int
    reward_soul: int
    reward_luck: int
    event_keys: tuple[str, ...]
    allowed_factions: tuple[str, ...] = ()


SECT_TASK_DEFINITIONS = (
    SectTaskDefinition(
        key="site_action_2",
        title="资源点差事",
        description="完成 2 次资源点操作。强争、护持、输运都算。",
        target=2,
        reward_lingshi=900,
        reward_soul=45,
        reward_luck=20,
        event_keys=("sect_site_action",),
    ),
    SectTaskDefinition(
        key="spar_1",
        title="切磋试手",
        description="完成 1 次切磋，不论胜负都算。",
        target=1,
        reward_lingshi=700,
        reward_soul=36,
        reward_luck=30,
        event_keys=("pvp_spar",),
    ),
    SectTaskDefinition(
        key="arena_1",
        title="擂台试锋",
        description="完成 1 次擂台出手。开擂、攻擂、收擂都算。",
        target=1,
        reward_lingshi=1200,
        reward_soul=60,
        reward_luck=40,
        event_keys=("pvp_arena",),
    ),
    SectTaskDefinition(
        key="bounty_1",
        title="悬赏讨伐",
        description="完成 1 次悬赏讨伐。",
        target=1,
        reward_lingshi=1400,
        reward_soul=72,
        reward_luck=50,
        event_keys=("faction_bounty_win",),
        allowed_factions=("righteous",),
    ),
    SectTaskDefinition(
        key="robbery_1",
        title="外道劫掠",
        description="完成 1 次劫掠得手。",
        target=1,
        reward_lingshi=1400,
        reward_soul=72,
        reward_luck=50,
        event_keys=("faction_robbery_win",),
        allowed_factions=("demonic",),
    ),
)


@dataclass(frozen=True, slots=True)
class SectSummary:
    sect_id: int
    name: str
    faction_name: str
    member_count: int
    owner_site_count: int
    leader_name: str


@dataclass(frozen=True, slots=True)
class SectOverview:
    sect_id: int
    name: str
    faction_name: str
    role_name: str
    member_count: int
    owner_site_count: int
    owned_site_names: tuple[str, ...]
    members: tuple["SectMemberView", ...]


@dataclass(frozen=True, slots=True)
class SectMemberView:
    character_id: int
    display_name: str
    role_name: str
    realm_display: str
    contribution_daily: int
    contribution_weekly: int
    contribution_total: int


@dataclass(frozen=True, slots=True)
class ResourceSiteView:
    site_id: int
    site_name: str
    site_type_name: str
    owner_name: str
    days_left: int
    guard_count: int
    transport_count: int
    required_progress: int
    attack_summaries: tuple[str, ...]
    player_role_name: str = ""
    player_progress: int = 0
    player_progress_required: int = 0


@dataclass(frozen=True, slots=True)
class SectTaskView:
    task_key: str
    title: str
    description: str
    progress: int
    target: int
    status_key: str
    status_name: str
    reward_lingshi: int
    reward_soul: int
    reward_luck: int


@dataclass(frozen=True, slots=True)
class SectTaskBoard:
    tasks: tuple[SectTaskView, ...]
    available_count: int
    active_count: int
    completed_count: int
    claimed_count: int


@dataclass(slots=True)
class SectActionResult:
    success: bool
    message: str
    contribution_gain: int = 0
    qi_before: int = 0
    qi_after: int = 0
    site_name: str = ""
    site_type_name: str = ""
    detail_lines: tuple[str, ...] = ()


class SectService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    async def ensure_sites(self, session: AsyncSession) -> None:
        today = today_shanghai()
        await self._initialize_legacy_sites(session, today)
        await self._spawn_sites_for_day(session, today)

    async def settle_sites_if_needed(self, session: AsyncSession) -> dict[int, list[str]]:
        today = today_shanghai()
        await self._initialize_legacy_sites(session, today)
        sites = list(
            (
                await session.scalars(
                    select(WorldResourceSite).options(selectinload(WorldResourceSite.owner_sect)).order_by(WorldResourceSite.id.asc())
                )
            ).all()
        )
        if not sites:
            await self._spawn_sites_for_day(session, today)
            await self._sync_character_contribution_dates(session, today)
            return {}

        characters = list(
            (
                await session.scalars(
                    select(Character).options(selectinload(Character.player), selectinload(Character.artifact), selectinload(Character.sect))
                )
            ).all()
        )
        characters_by_id = {character.id: character for character in characters}
        notices: dict[int, list[str]] = {}

        for site in sites:
            state = self._load_state(site.state_json)
            self._sanitize_site_state(site, state, characters_by_id)
            if site.settlement_day is None:
                site.settlement_day = today
            elif site.settlement_day < today:
                reward_day = site.settlement_day
                if site.owner_sect_id is not None and site.expires_on is not None and reward_day <= site.expires_on:
                    transport_count = len(state["transport_member_ids"])
                    for character in characters:
                        if not self._can_receive_site_reward(character, site, reward_day):
                            continue
                        reward_lines = self._apply_site_reward(character, site, transport_count)
                        if reward_lines:
                            notices.setdefault(character.id, []).extend(reward_lines)
                            character.sect_last_settlement_on = reward_day
                            character.sect_last_settlement_summary = "\n".join(notices[character.id])
                site.settlement_day = today
            site.state_json = self._dump_state(state)

        await self._cleanup_expired_sites(session, today, characters_by_id)
        await self._spawn_sites_for_day(session, today)
        await self._sync_character_contribution_dates(session, today)
        await session.flush()
        return notices

    async def create_sect(self, session: AsyncSession, character: Character, name: str) -> tuple[bool, str]:
        if character.sect_id is not None:
            return False, "你既已归于一门，不可再立新宗。"
        if character.realm_index < 2:
            return False, "修为尚浅，还不足以立下一门旗号。"
        cleaned = re.sub(r"\s+", "", name).strip()
        if len(cleaned) < 2 or len(cleaned) > 12:
            return False, "宗门之名需在 2 到 12 个字符之间。"
        if "@everyone" in cleaned or "@here" in cleaned:
            return False, "此名太过张扬，不可立作宗门旗号。"
        existing = await session.scalar(select(Sect).where(Sect.name == cleaned))
        if existing is not None:
            return False, "此门之名已有人占下。"

        sect = Sect(name=cleaned, faction_key=character.faction, founder_character_id=character.id)
        session.add(sect)
        await session.flush()
        now = now_shanghai()
        character.sect = sect
        character.sect_id = sect.id
        character.sect_joined_at = now
        character.sect_contribution_total = 0
        character.sect_contribution_weekly = 0
        character.sect_contribution_daily = 0
        character.sect_last_contribution_on = None
        character.sect_bound_site_id = None
        character.sect_bound_site_role = None
        character.sect_last_settlement_on = None
        character.sect_last_settlement_summary = ""
        character.sect_task_refresh_on = None
        character.sect_task_state_json = ""
        character.last_highlight_text = f"方才立下宗门「{cleaned}」。"
        return True, f"你已立下宗门「{cleaned}」。"

    async def list_joinable_sects(self, session: AsyncSession, character: Character, *, limit: int = 10) -> list[SectSummary]:
        await self.ensure_sites(session)
        sects = list(
            (
                await session.scalars(
                    select(Sect).options(selectinload(Sect.members).selectinload(Character.player), selectinload(Sect.resource_sites))
                )
            ).all()
        )
        result: list[SectSummary] = []
        today = today_shanghai()
        for sect in sects:
            if len(sect.members) >= SECT_MEMBER_LIMIT:
                continue
            if character.faction != "neutral" and sect.faction_key != character.faction:
                continue
            active_owned_count = sum(1 for site in sect.resource_sites if site.owner_sect_id == sect.id and self._site_is_active(site, today))
            result.append(
                SectSummary(
                    sect_id=sect.id,
                    name=sect.name,
                    faction_name=FACTION_NAMES.get(sect.faction_key, "中立"),
                    member_count=len(sect.members),
                    owner_site_count=active_owned_count,
                    leader_name=next((member.player.display_name for member in sect.members if member.id == sect.founder_character_id), "暂无宗主"),
                )
            )
        result.sort(key=lambda item: (-item.owner_site_count, item.member_count, item.sect_id))
        return result[:limit]

    async def get_sect_overview(self, session: AsyncSession, character: Character) -> SectOverview | None:
        if character.sect_id is None:
            return None
        sect = await session.scalar(
            select(Sect)
            .where(Sect.id == character.sect_id)
            .options(selectinload(Sect.members).selectinload(Character.player), selectinload(Sect.resource_sites))
        )
        if sect is None:
            return None
        sect_name, role_name = await self.get_member_identity(session, character)
        today = today_shanghai()
        owned_sites = tuple(site.site_name for site in sect.resource_sites if site.owner_sect_id == sect.id and self._site_is_active(site, today))
        member_views = self._build_member_views(sect)
        return SectOverview(
            sect_id=sect.id,
            name=sect_name or sect.name,
            faction_name=FACTION_NAMES.get(sect.faction_key, "中立"),
            role_name=role_name,
            member_count=len(sect.members),
            owner_site_count=len(owned_sites),
            owned_site_names=owned_sites,
            members=member_views,
        )

    def get_task_board(self, character: Character) -> SectTaskBoard:
        task_state = self._ensure_task_state(character)
        tasks: list[SectTaskView] = []
        counts = {
            "available": 0,
            "active": 0,
            "completed": 0,
            "claimed": 0,
        }
        task_entries = task_state.get("tasks", {})
        for definition in self._iter_task_definitions(character):
            entry = task_entries.get(definition.key, {"progress": 0, "status": "available"})
            status_key = str(entry.get("status", "available"))
            progress = min(definition.target, max(0, int(entry.get("progress", 0))))
            if progress >= definition.target and status_key == "active":
                status_key = "completed"
                entry["status"] = status_key
            if status_key not in TASK_STATUS_NAMES:
                status_key = "available"
                entry["status"] = status_key
            entry["progress"] = progress
            counts[status_key] += 1
            tasks.append(
                SectTaskView(
                    task_key=definition.key,
                    title=definition.title,
                    description=definition.description,
                    progress=progress,
                    target=definition.target,
                    status_key=status_key,
                    status_name=TASK_STATUS_NAMES[status_key],
                    reward_lingshi=definition.reward_lingshi,
                    reward_soul=definition.reward_soul,
                    reward_luck=definition.reward_luck,
                )
            )
        self._save_task_state(character, task_state)
        return SectTaskBoard(
            tasks=tuple(tasks),
            available_count=counts["available"],
            active_count=counts["active"],
            completed_count=counts["completed"],
            claimed_count=counts["claimed"],
        )

    def task_summary_text(self, character: Character) -> str:
        board = self.get_task_board(character)
        parts: list[str] = []
        if board.available_count > 0:
            parts.append(f"可领取 {board.available_count}")
        if board.active_count > 0:
            parts.append(f"进行中 {board.active_count}")
        if board.completed_count > 0:
            parts.append(f"可领奖 {board.completed_count}")
        if not parts:
            parts.append("今日已全数领完")
        return " · ".join(parts)

    def accept_task(self, character: Character, task_key: str) -> tuple[bool, str]:
        if character.sect_id is None:
            return False, "你尚未归于任何宗门。"
        task_state = self._ensure_task_state(character)
        definition = self._task_definition_for(character, task_key)
        if definition is None:
            return False, "未找到该宗门任务。"
        task_entry = task_state["tasks"].get(task_key)
        if task_entry is None:
            return False, "未找到该宗门任务。"
        status_key = str(task_entry.get("status", "available"))
        if status_key == "claimed":
            return False, "该任务奖励今日已领过。"
        if status_key == "completed":
            return False, "该任务已完成，可直接领奖。"
        if status_key == "active":
            return False, "该任务已在进行中。"
        task_entry["status"] = "active"
        task_entry["progress"] = min(definition.target, max(0, int(task_entry.get("progress", 0))))
        self._save_task_state(character, task_state)
        return True, f"你已领取任务「{definition.title}」。"

    def claim_task_reward(self, character: Character, task_key: str) -> tuple[bool, str]:
        if character.sect_id is None:
            return False, "你尚未归于任何宗门。"
        task_state = self._ensure_task_state(character)
        definition = self._task_definition_for(character, task_key)
        if definition is None:
            return False, "未找到该宗门任务。"
        task_entry = task_state["tasks"].get(task_key)
        if task_entry is None:
            return False, "未找到该宗门任务。"
        status_key = str(task_entry.get("status", "available"))
        if status_key == "claimed":
            return False, "该任务奖励今日已领过。"
        if status_key != "completed":
            return False, "该任务尚未完成，暂不可领奖。"
        if definition.reward_lingshi > 0:
            character.lingshi += definition.reward_lingshi
        if definition.reward_soul > 0 and character.artifact is not None:
            character.artifact.soul_shards += definition.reward_soul
        if definition.reward_luck > 0:
            character.luck += definition.reward_luck
        task_entry["status"] = "claimed"
        self._save_task_state(character, task_state)
        reward_text = self._format_task_reward(definition)
        character.last_highlight_text = f"方才领下宗门任务「{definition.title}」的赏赐。"
        return True, f"你已领取「{definition.title}」奖励：{reward_text}。"

    def record_task_event(self, character: Character, event_key: str, *, amount: int = 1) -> None:
        if character.sect_id is None or amount <= 0:
            return
        task_state = self._ensure_task_state(character)
        task_entries = task_state.get("tasks", {})
        changed = False
        for definition in self._iter_task_definitions(character):
            if event_key not in definition.event_keys:
                continue
            task_entry = task_entries.get(definition.key)
            if task_entry is None or str(task_entry.get("status", "available")) != "active":
                continue
            progress_before = max(0, int(task_entry.get("progress", 0)))
            progress_after = min(definition.target, progress_before + amount)
            if progress_after == progress_before:
                continue
            task_entry["progress"] = progress_after
            if progress_after >= definition.target:
                task_entry["status"] = "completed"
            changed = True
        if changed:
            self._save_task_state(character, task_state)

    async def join_sect(self, session: AsyncSession, character: Character, sect_id: int) -> tuple[bool, str]:
        if character.sect_id is not None:
            return False, "你既已入门，不可再投他宗。"
        if character.sect_last_left_at is not None:
            remaining = ensure_shanghai(character.sect_last_left_at) + timedelta(hours=SECT_JOIN_COOLDOWN_HOURS) - now_shanghai()
            if remaining.total_seconds() > 0:
                hours = max(1, int(remaining.total_seconds() // 3600))
                return False, f"离宗余波未散，还需再等 {hours} 小时。"
        sect = await session.scalar(select(Sect).where(Sect.id == sect_id).options(selectinload(Sect.members)))
        if sect is None:
            return False, "此门踪影已散，暂不可投身。"
        if len(sect.members) >= SECT_MEMBER_LIMIT:
            return False, "此门门庭已满，今日不再收人。"
        if character.faction != "neutral" and sect.faction_key != character.faction:
            return False, "阵营不同，此门不会收下你。"
        now = now_shanghai()
        character.sect = sect
        character.sect_id = sect.id
        character.sect_joined_at = now
        character.sect_contribution_daily = 0
        character.sect_contribution_weekly = 0
        character.sect_last_contribution_on = None
        character.sect_bound_site_id = None
        character.sect_bound_site_role = None
        character.sect_last_settlement_on = None
        character.sect_last_settlement_summary = ""
        character.sect_task_refresh_on = None
        character.sect_task_state_json = ""
        character.last_highlight_text = f"方才投入宗门「{sect.name}」。"
        return True, f"你已投入宗门「{sect.name}」。"

    async def leave_sect(self, session: AsyncSession, character: Character) -> tuple[bool, str]:
        if character.sect_id is None:
            return False, "你本就未归于任何宗门。"
        sect = await session.scalar(
            select(Sect).where(Sect.id == character.sect_id).options(selectinload(Sect.members), selectinload(Sect.resource_sites))
        )
        if sect is None:
            character.sect = None
            character.sect_id = None
            character.sect_bound_site_id = None
            character.sect_bound_site_role = None
            character.sect_last_settlement_on = None
            character.sect_last_settlement_summary = ""
            character.sect_task_refresh_on = None
            character.sect_task_state_json = ""
            return True, "旧门已散，你自行抽身而出。"

        await self._detach_character_from_site(session, character)
        leaving_name = sect.name
        character.sect = None
        character.sect_id = None
        character.sect_joined_at = None
        character.sect_last_left_at = now_shanghai()
        character.sect_contribution_daily = 0
        character.sect_contribution_weekly = 0
        character.sect_last_contribution_on = None
        character.sect_bound_site_id = None
        character.sect_bound_site_role = None
        character.sect_last_settlement_on = None
        character.sect_last_settlement_summary = ""
        character.sect_task_refresh_on = None
        character.sect_task_state_json = ""
        await session.flush()

        remaining_members = list((await session.scalars(select(Character).where(Character.sect_id == sect.id))).all())
        if not remaining_members:
            for site in sect.resource_sites:
                state = self._load_state(site.state_json)
                state["guard_member_ids"] = []
                state["transport_member_ids"] = []
                state["attack_members"].pop(sect.id, None)
                state["attack_progress"].pop(sect.id, None)
                if site.owner_sect_id == sect.id:
                    site.owner_sect_id = None
                site.state_json = self._dump_state(state)
            await session.delete(sect)
        elif sect.founder_character_id == character.id:
            replacement = max(
                remaining_members,
                key=lambda member: (member.sect_contribution_weekly, member.sect_contribution_total, -member.id),
            )
            sect.founder_character_id = replacement.id
        character.last_highlight_text = f"方才离开宗门「{leaving_name}」。"
        return True, f"你已离开宗门「{leaving_name}」。"

    async def get_member_identity(self, session: AsyncSession, character: Character) -> tuple[str, str]:
        if character.sect_id is None:
            return ("", "")
        sect = character.sect or await session.get(Sect, character.sect_id)
        if sect is None:
            return ("", "")
        members = list((await session.scalars(select(Character).where(Character.sect_id == character.sect_id))).all())
        members.sort(key=lambda entry: (-entry.sect_contribution_weekly, -entry.sect_contribution_total, entry.id))
        if character.id == sect.founder_character_id:
            return (sect.name, "宗主")
        others = [entry for entry in members if entry.id != sect.founder_character_id]
        if others and others[0].id == character.id:
            return (sect.name, "副宗主")
        if any(entry.id == character.id for entry in others[1:4]):
            return (sect.name, "长老")
        return (sect.name, "弟子")

    async def list_sites(self, session: AsyncSession, character: Character | None = None) -> list[ResourceSiteView]:
        await self.ensure_sites(session)
        today = today_shanghai()
        sites = list(
            (
                await session.scalars(
                    select(WorldResourceSite).options(selectinload(WorldResourceSite.owner_sect)).where(WorldResourceSite.expires_on.is_not(None)).order_by(WorldResourceSite.expires_on.asc(), WorldResourceSite.id.asc())
                )
            ).all()
        )
        sites = [site for site in sites if self._site_is_active(site, today)]
        sect_names = {sect.id: sect.name for sect in (await session.scalars(select(Sect))).all()}
        characters_by_id: dict[int, Character] = {}
        if sites:
            member_ids = set()
            for site in sites:
                state = self._load_state(site.state_json)
                member_ids.update(self._collect_state_member_ids(state))
            if member_ids:
                characters = list((await session.scalars(select(Character).where(Character.id.in_(member_ids)))).all())
                characters_by_id = {entry.id: entry for entry in characters}

        views: list[ResourceSiteView] = []
        for site in sites:
            state = self._load_state(site.state_json)
            self._sanitize_site_state(site, state, characters_by_id)
            site.state_json = self._dump_state(state)
            required_progress = UNOWNED_SITE_PROGRESS_TARGET if site.owner_sect_id is None else OWNED_SITE_PROGRESS_TARGET
            player_progress = 0
            player_progress_required = 0
            player_role_name = ""
            if character is not None:
                if character.sect_bound_site_id == site.id:
                    player_role_name = SITE_ROLE_NAMES.get(character.sect_bound_site_role or "", "")
                if character.sect_id is not None:
                    player_progress = int(state["attack_progress"].get(character.sect_id, 0))
                    if player_progress > 0:
                        player_progress_required = required_progress
            attack_summaries = self._build_attack_summaries(state["attack_progress"], required_progress, sect_names)
            owner_name = site.owner_sect.name if site.owner_sect is not None else "无主"
            views.append(
                ResourceSiteView(
                    site_id=site.id,
                    site_name=site.site_name,
                    site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
                    owner_name=owner_name,
                    days_left=max(1, (site.expires_on - today).days + 1) if site.expires_on is not None else 1,
                    guard_count=len(state["guard_member_ids"]),
                    transport_count=len(state["transport_member_ids"]),
                    required_progress=required_progress,
                    attack_summaries=attack_summaries,
                    player_role_name=player_role_name,
                    player_progress=player_progress,
                    player_progress_required=player_progress_required,
                )
            )
        return views

    async def perform_site_action(self, session: AsyncSession, character: Character, site_id: int, action_key: str) -> SectActionResult:
        if character.sect_id is None:
            return SectActionResult(False, "你尚未归于任何宗门。")
        if action_key not in SITE_ACTION_LABELS:
            return SectActionResult(False, "无效的资源点操作。")
        if character.current_qi < SECT_ACTION_QI_COST:
            return SectActionResult(False, "气机不足，无法进行资源点操作。", qi_before=character.current_qi, qi_after=character.current_qi)

        today = today_shanghai()
        await self.ensure_sites(session)
        site = await session.scalar(select(WorldResourceSite).where(WorldResourceSite.id == site_id).options(selectinload(WorldResourceSite.owner_sect)))
        if site is None or not self._site_is_active(site, today):
            return SectActionResult(False, "该资源点已失效。")

        state = self._load_state(site.state_json)
        await self._hydrate_site_state(session, site, state)
        blocked = self._validate_site_action(character, site, action_key)
        if blocked is not None:
            return SectActionResult(
                False,
                blocked,
                qi_before=character.current_qi,
                qi_after=character.current_qi,
                site_name=site.site_name,
                site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
            )

        if action_key in {"guard", "transport"} and character.sect_bound_site_id == site.id and character.sect_bound_site_role == action_key:
            return SectActionResult(
                False,
                f"你已在此地{SITE_ACTION_LABELS[action_key]}。",
                qi_before=character.current_qi,
                qi_after=character.current_qi,
                site_name=site.site_name,
                site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
            )

        self._sync_single_character_contribution(character, today)
        qi_before = character.current_qi
        character.current_qi -= SECT_ACTION_QI_COST

        contribution = self._calculate_contribution(character, action_key)
        character.sect_contribution_total += contribution
        character.sect_contribution_weekly += contribution
        character.sect_contribution_daily += contribution
        character.sect_last_contribution_on = today

        await self._detach_character_from_site(session, character, current_site=site, current_state=state)

        if action_key == "contest":
            result = await self._perform_contest(session, character, site, state, contribution, qi_before)
        elif action_key == "guard":
            self._add_guard_member(state, character.id)
            character.sect_bound_site_id = site.id
            character.sect_bound_site_role = "guard"
            character.last_highlight_text = f"已在 {site.site_name} 执行护持。"
            result = SectActionResult(
                True,
                f"你已在 {site.site_name} 执行护持。",
                contribution_gain=contribution,
                qi_before=qi_before,
                qi_after=character.current_qi,
                site_name=site.site_name,
                site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
                detail_lines=(f"护持人数：`{len(state['guard_member_ids'])}`", f"宗门贡献：`+{contribution}`"),
            )
        else:
            self._add_transport_member(state, character.id)
            character.sect_bound_site_id = site.id
            character.sect_bound_site_role = "transport"
            character.last_highlight_text = f"已在 {site.site_name} 执行输运。"
            result = SectActionResult(
                True,
                f"你已在 {site.site_name} 执行输运。",
                contribution_gain=contribution,
                qi_before=qi_before,
                qi_after=character.current_qi,
                site_name=site.site_name,
                site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
                detail_lines=(f"输运人数：`{len(state['transport_member_ids'])}`", f"宗门贡献：`+{contribution}`"),
            )

        site.state_json = self._dump_state(state)
        self.record_task_event(character, "sect_site_action")
        await session.flush()
        return result

    def _build_member_views(self, sect: Sect) -> tuple[SectMemberView, ...]:
        members = list(sect.members)
        members.sort(
            key=lambda entry: (
                0 if entry.id == sect.founder_character_id else 1,
                -(entry.sect_contribution_weekly or 0),
                -(entry.sect_contribution_total or 0),
                entry.id,
            )
        )
        views: list[SectMemberView] = []
        founder_id = sect.founder_character_id
        others = [entry for entry in members if entry.id != founder_id]
        vice_id = others[0].id if others else None
        elder_ids = {entry.id for entry in others[1:4]}
        for member in members[:25]:
            if member.id == founder_id:
                role_name = "宗主"
            elif vice_id is not None and member.id == vice_id:
                role_name = "副宗主"
            elif member.id in elder_ids:
                role_name = "长老"
            else:
                role_name = "弟子"
            stage = get_stage(member.realm_key, member.stage_key)
            views.append(
                SectMemberView(
                    character_id=member.id,
                    display_name=member.player.display_name,
                    role_name=role_name,
                    realm_display=stage.display_name,
                    contribution_daily=member.sect_contribution_daily or 0,
                    contribution_weekly=member.sect_contribution_weekly or 0,
                    contribution_total=member.sect_contribution_total or 0,
                )
            )
        return tuple(views)

    async def _perform_contest(
        self,
        session: AsyncSession,
        character: Character,
        site: WorldResourceSite,
        state: dict[str, object],
        contribution: int,
        qi_before: int,
    ) -> SectActionResult:
        attack_members = state["attack_members"].setdefault(character.sect_id, [])
        if character.id not in attack_members:
            attack_members.append(character.id)
        character.sect_bound_site_id = site.id
        character.sect_bound_site_role = "contest"

        required_progress = UNOWNED_SITE_PROGRESS_TARGET if site.owner_sect_id is None else OWNED_SITE_PROGRESS_TARGET
        detail_lines: list[str] = [f"宗门贡献：`+{contribution}`"]

        if site.owner_sect_id is None:
            current_progress = int(state["attack_progress"].get(character.sect_id, 0)) + 1
            state["attack_progress"][character.sect_id] = current_progress
            detail_lines.insert(0, f"占领进度：`{current_progress}/{required_progress}`")
            if current_progress >= required_progress:
                await self._claim_site(session, site, state, character.sect_id)
                character.last_highlight_text = f"已为宗门占领 {site.site_name}。"
                detail_lines.insert(0, "该资源点已由本宗占领。")
                message = f"你所在宗门已占领 {site.site_name}。"
            else:
                character.last_highlight_text = f"已推进 {site.site_name} 的占领进度。"
                message = f"你推进了 {site.site_name} 的占领进度。"
            return SectActionResult(
                True,
                message,
                contribution_gain=contribution,
                qi_before=qi_before,
                qi_after=character.current_qi,
                site_name=site.site_name,
                site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
                detail_lines=tuple(detail_lines),
            )

        attack_power = await self._calculate_side_power(session, attack_members)
        defense_power = await self._calculate_side_power(session, state["guard_member_ids"])
        contest_won, clash_line = self._resolve_contest(attack_power, defense_power)
        detail_lines.insert(0, clash_line)
        current_progress = int(state["attack_progress"].get(character.sect_id, 0))
        if contest_won:
            current_progress += 1
            state["attack_progress"][character.sect_id] = current_progress
        detail_lines.insert(1, f"争夺进度：`{current_progress}/{required_progress}`")

        if contest_won and current_progress >= required_progress:
            await self._claim_site(session, site, state, character.sect_id)
            character.last_highlight_text = f"已为宗门夺取 {site.site_name}。"
            detail_lines.insert(0, "该资源点已由本宗夺取。")
            message = f"你所在宗门已夺取 {site.site_name}。"
        elif contest_won:
            character.last_highlight_text = f"已推进 {site.site_name} 的争夺进度。"
            message = f"你推进了 {site.site_name} 的争夺进度。"
        else:
            character.last_highlight_text = f"在 {site.site_name} 的强争失败。"
            message = f"你未能推进 {site.site_name} 的争夺进度。"

        return SectActionResult(
            contest_won,
            message,
            contribution_gain=contribution,
            qi_before=qi_before,
            qi_after=character.current_qi,
            site_name=site.site_name,
            site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
            detail_lines=tuple(detail_lines),
        )

    async def _claim_site(self, session: AsyncSession, site: WorldResourceSite, state: dict[str, object], owner_sect_id: int) -> None:
        previous_member_ids = set(state["guard_member_ids"]) | set(state["transport_member_ids"])
        for sect_id, member_ids in state["attack_members"].items():
            if sect_id != owner_sect_id:
                previous_member_ids.update(member_ids)
        winning_member_ids = self._dedupe_ids(state["attack_members"].get(owner_sect_id, []))
        await self._clear_bound_characters(session, previous_member_ids, site.id)
        await self._set_bound_characters(session, winning_member_ids, site.id, "guard")
        site.owner_sect_id = owner_sect_id
        state["attack_progress"] = {}
        state["attack_members"] = {}
        state["guard_member_ids"] = winning_member_ids
        state["transport_member_ids"] = []

    def _validate_site_action(self, character: Character, site: WorldResourceSite, action_key: str) -> str | None:
        if action_key == "contest":
            if site.owner_sect_id == character.sect_id:
                return "该资源点已由本宗占有，可执行护持或输运。"
            return None
        if site.owner_sect_id is None:
            return "该资源点当前无归属，只能执行强争。"
        if site.owner_sect_id != character.sect_id:
            return "该资源点不属于本宗，当前只能执行强争。"
        return None

    def _calculate_contribution(self, character: Character, action_key: str) -> int:
        stage = get_stage(character.realm_key, character.stage_key)
        if action_key == "contest":
            value = 60 + stage.global_stage_index * 3 + self._random_int(0, 15)
        elif action_key == "guard":
            value = 50 + stage.global_stage_index * 2 + self._random_int(0, 10)
        else:
            value = 40 + self._random_int(0, 5)
        return max(1, value)

    def _apply_site_reward(self, character: Character, site: WorldResourceSite, transport_count: int) -> list[str]:
        config = SITE_REWARD_CONFIG[site.site_type]
        multiplier = 1 + 0.1 * transport_count
        parts: list[str] = []
        if config.get("lingshi", 0):
            reward_lingshi = max(1, int(config["lingshi"] * multiplier))
            character.lingshi += reward_lingshi
            parts.append(f"灵石 +{reward_lingshi}")
        if config.get("soul", 0) and character.artifact is not None:
            reward_soul = max(1, int(config["soul"] * multiplier))
            character.artifact.soul_shards += reward_soul
            parts.append(f"器魂 +{reward_soul}")
        if not parts:
            return []
        return [f"{site.site_name}：{' · '.join(parts)}"]

    async def _sync_character_contribution_dates(self, session: AsyncSession, today) -> None:
        characters = list((await session.scalars(select(Character).where(Character.sect_last_contribution_on.is_not(None)))).all())
        for character in characters:
            self._sync_single_character_contribution(character, today)

    @staticmethod
    def _sync_single_character_contribution(character: Character, today) -> None:
        last_day = character.sect_last_contribution_on
        if last_day is None or last_day >= today:
            return
        if last_day.isocalendar()[:2] != today.isocalendar()[:2]:
            character.sect_contribution_weekly = 0
        character.sect_contribution_daily = 0

    async def _initialize_legacy_sites(self, session: AsyncSession, today) -> None:
        sites = list((await session.scalars(select(WorldResourceSite))).all())
        changed = False
        used_names_by_type = {site_type: set() for site_type in SITE_NAME_POOLS}
        for site in sites:
            if site.site_type in SITE_NAME_POOLS and site.site_name in SITE_NAME_POOLS[site.site_type]:
                used_names_by_type.setdefault(site.site_type, set()).add(site.site_name)
        for site in sites:
            # 兼容旧档中的灵泉资源点，统一迁回当前保留的资源类型。
            if site.site_type not in SITE_TYPE_NAMES:
                fallback_type = LEGACY_SITE_TYPE_FALLBACK
                site.site_type = fallback_type
                site.site_name = self._roll_site_name(fallback_type, used_names_by_type.setdefault(fallback_type, set()))
                used_names_by_type[fallback_type].add(site.site_name)
                changed = True
            if site.spawned_on is None:
                site.spawned_on = site.settlement_day or today
                changed = True
            if site.expires_on is None and site.spawned_on is not None:
                site.expires_on = site.spawned_on + timedelta(days=SITE_LIFETIME_DAYS - 1)
                changed = True
            if site.settlement_day is None:
                site.settlement_day = today
                changed = True
            if not site.state_json:
                site.state_json = self._dump_state(self._empty_state())
                changed = True
        if changed:
            await session.flush()

    async def _spawn_sites_for_day(self, session: AsyncSession, today) -> None:
        spawned_today = list((await session.scalars(select(WorldResourceSite).where(WorldResourceSite.spawned_on == today))).all())
        missing = max(0, DAILY_SITE_SPAWN_COUNT - len(spawned_today))
        if missing <= 0:
            return
        active_sites = list((await session.scalars(select(WorldResourceSite).where(WorldResourceSite.expires_on.is_not(None)))).all())
        existing_count = len(active_sites)
        used_names_by_type: dict[str, set[str]] = {}
        for site in active_sites:
            if site.expires_on is None or site.expires_on < today:
                continue
            used_names_by_type.setdefault(site.site_type, set()).add(site.site_name)

        weighted_types = [site_type for site_type, weight in SITE_TYPE_WEIGHTS for _ in range(weight)]
        for index in range(missing):
            site_type = self.rng.choice(weighted_types)
            used_names = used_names_by_type.setdefault(site_type, set())
            site_name = self._roll_site_name(site_type, used_names)
            used_names.add(site_name)
            site_key = f"{today.isoformat()}_{existing_count + index + 1}_{site_type}"
            session.add(
                WorldResourceSite(
                    site_key=site_key,
                    site_name=site_name,
                    site_type=site_type,
                    owner_sect_id=None,
                    spawned_on=today,
                    expires_on=today + timedelta(days=SITE_LIFETIME_DAYS - 1),
                    settlement_day=today,
                    state_json=self._dump_state(self._empty_state()),
                )
            )
        await session.flush()

    async def _cleanup_expired_sites(self, session: AsyncSession, today, characters_by_id: dict[int, Character] | None = None) -> None:
        expired_sites = list((await session.scalars(select(WorldResourceSite).where(WorldResourceSite.expires_on.is_not(None), WorldResourceSite.expires_on < today))).all())
        for site in expired_sites:
            state = self._load_state(site.state_json)
            member_ids = self._collect_state_member_ids(state)
            if characters_by_id:
                for member_id in member_ids:
                    character = characters_by_id.get(member_id)
                    if character is not None and character.sect_bound_site_id == site.id:
                        character.sect_bound_site_id = None
                        character.sect_bound_site_role = None
            await self._clear_bound_characters(session, member_ids, site.id)
            await session.delete(site)

    async def _detach_character_from_site(
        self,
        session: AsyncSession,
        character: Character,
        *,
        current_site: WorldResourceSite | None = None,
        current_state: dict[str, object] | None = None,
    ) -> None:
        bound_site_id = character.sect_bound_site_id
        if bound_site_id is None:
            character.sect_bound_site_role = None
            return
        if current_site is not None and current_state is not None and current_site.id == bound_site_id:
            self._remove_member_from_state(current_state, character.id)
        else:
            old_site = await session.get(WorldResourceSite, bound_site_id)
            if old_site is not None:
                old_state = self._load_state(old_site.state_json)
                self._remove_member_from_state(old_state, character.id)
                old_site.state_json = self._dump_state(old_state)
        character.sect_bound_site_id = None
        character.sect_bound_site_role = None

    async def _hydrate_site_state(self, session: AsyncSession, site: WorldResourceSite, state: dict[str, object]) -> None:
        member_ids = self._collect_state_member_ids(state)
        if not member_ids:
            self._sanitize_site_state(site, state, {})
            return
        characters = list((await session.scalars(select(Character).where(Character.id.in_(member_ids)))).all())
        self._sanitize_site_state(site, state, {entry.id: entry for entry in characters})

    def _sanitize_site_state(self, site: WorldResourceSite, state: dict[str, object], characters_by_id: dict[int, Character]) -> None:
        owner_sect_id = site.owner_sect_id
        if owner_sect_id is None:
            state["guard_member_ids"] = []
            state["transport_member_ids"] = []
        else:
            state["guard_member_ids"] = [
                member_id
                for member_id in self._dedupe_ids(state["guard_member_ids"])
                if self._member_matches_site(characters_by_id.get(member_id), site.id, owner_sect_id, "guard")
            ]
            state["transport_member_ids"] = [
                member_id
                for member_id in self._dedupe_ids(state["transport_member_ids"])
                if self._member_matches_site(characters_by_id.get(member_id), site.id, owner_sect_id, "transport")
            ]

        sanitized_attack_members: dict[int, list[int]] = {}
        sanitized_attack_progress: dict[int, int] = {}
        for raw_sect_id, raw_members in state["attack_members"].items():
            sect_id = int(raw_sect_id)
            if sect_id == owner_sect_id:
                continue
            valid_members = [
                member_id
                for member_id in self._dedupe_ids(raw_members)
                if self._member_matches_site(characters_by_id.get(member_id), site.id, sect_id, "contest")
            ]
            if valid_members:
                sanitized_attack_members[sect_id] = valid_members
            progress = int(state["attack_progress"].get(sect_id, 0))
            if progress > 0:
                sanitized_attack_progress[sect_id] = progress
        state["attack_members"] = sanitized_attack_members
        state["attack_progress"] = sanitized_attack_progress

    def _member_matches_site(self, character: Character | None, site_id: int, sect_id: int, role: str) -> bool:
        if character is None:
            return False
        return character.sect_id == sect_id and character.sect_bound_site_id == site_id and character.sect_bound_site_role == role

    async def _calculate_side_power(self, session: AsyncSession, member_ids: list[int]) -> int:
        if not member_ids:
            return 0
        characters = list((await session.scalars(select(Character).where(Character.id.in_(member_ids)))).all())
        return sum(self._base_power(entry) for entry in characters)

    def _base_power(self, character: Character) -> int:
        stage = get_stage(character.realm_key, character.stage_key)
        return int(stage.base_atk * 1.35 + stage.base_def * 1.5 + stage.base_agi * 1.15)

    def _resolve_contest(self, attack_power: int, defense_power: int) -> tuple[bool, str]:
        if defense_power <= 0:
            return True, f"战力对比：攻 {attack_power} / 守 0，对方当前无人护持。"
        ratio = attack_power / max(defense_power, 1)
        if ratio >= DIRECT_WIN_RATIO:
            return True, f"战力对比：攻 {attack_power} / 守 {defense_power}，本次强争成功。"
        if ratio <= DIRECT_LOSS_RATIO:
            return False, f"战力对比：攻 {attack_power} / 守 {defense_power}，本次强争失败。"
        win_rate = max(SWING_WIN_RATE_MIN, min(SWING_WIN_RATE_MAX, 0.5 + (ratio - 1.0) * 0.5))
        if self.rng.random() < win_rate:
            return True, f"战力对比：攻 {attack_power} / 守 {defense_power}，双方接近，但本次强争成功。"
        return False, f"战力对比：攻 {attack_power} / 守 {defense_power}，双方接近，但本次强争失败。"

    def _can_receive_site_reward(self, character: Character, site: WorldResourceSite, reward_day) -> bool:
        if character.sect_id != site.owner_sect_id:
            return False
        if character.sect_joined_at is None or ensure_shanghai(character.sect_joined_at).date() > reward_day:
            return False
        if character.sect_bound_site_id == site.id and character.sect_bound_site_role in {"guard", "transport"}:
            return True
        return character.sect_last_contribution_on == reward_day and (character.sect_contribution_daily or 0) > 0

    def _iter_task_definitions(self, character: Character) -> tuple[SectTaskDefinition, ...]:
        available: list[SectTaskDefinition] = []
        for definition in SECT_TASK_DEFINITIONS:
            if definition.allowed_factions and character.faction not in definition.allowed_factions:
                continue
            available.append(definition)
        return tuple(available)

    def _task_definition_for(self, character: Character, task_key: str) -> SectTaskDefinition | None:
        for definition in self._iter_task_definitions(character):
            if definition.key == task_key:
                return definition
        return None

    @staticmethod
    def _empty_task_state() -> dict[str, object]:
        return {"tasks": {}}

    def _load_task_state(self, raw_json: str | None) -> dict[str, object]:
        state = self._empty_task_state()
        if not raw_json:
            return state
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return state
        if not isinstance(payload, dict):
            return state
        raw_tasks = payload.get("tasks", {})
        if not isinstance(raw_tasks, dict):
            return state
        tasks: dict[str, dict[str, object]] = {}
        for raw_key, raw_entry in raw_tasks.items():
            if not isinstance(raw_key, str) or not isinstance(raw_entry, dict):
                continue
            status_key = str(raw_entry.get("status", "available"))
            if status_key not in TASK_STATUS_NAMES:
                status_key = "available"
            progress = max(0, int(raw_entry.get("progress", 0)))
            tasks[raw_key] = {
                "status": status_key,
                "progress": progress,
            }
        state["tasks"] = tasks
        return state

    def _ensure_task_state(self, character: Character) -> dict[str, object]:
        if character.sect_id is None:
            character.sect_task_refresh_on = None
            character.sect_task_state_json = ""
            return self._empty_task_state()
        today = today_shanghai()
        definitions = self._iter_task_definitions(character)
        if character.sect_task_refresh_on != today:
            state = {
                "tasks": {
                    definition.key: {"status": "available", "progress": 0}
                    for definition in definitions
                }
            }
            character.sect_task_refresh_on = today
            self._save_task_state(character, state)
            return state
        state = self._load_task_state(character.sect_task_state_json)
        task_entries: dict[str, dict[str, object]] = {}
        raw_entries = state.get("tasks", {})
        if isinstance(raw_entries, dict):
            for definition in definitions:
                entry = raw_entries.get(definition.key, {})
                if not isinstance(entry, dict):
                    entry = {}
                status_key = str(entry.get("status", "available"))
                if status_key not in TASK_STATUS_NAMES:
                    status_key = "available"
                progress = min(definition.target, max(0, int(entry.get("progress", 0))))
                if progress >= definition.target and status_key == "active":
                    status_key = "completed"
                task_entries[definition.key] = {
                    "status": status_key,
                    "progress": progress,
                }
        state["tasks"] = task_entries
        self._save_task_state(character, state)
        return state

    def _save_task_state(self, character: Character, payload: dict[str, object]) -> None:
        normalized = {
            "tasks": {
                str(key): {
                    "status": str(value.get("status", "available")),
                    "progress": max(0, int(value.get("progress", 0))),
                }
                for key, value in payload.get("tasks", {}).items()
                if isinstance(value, dict)
            }
        }
        character.sect_task_state_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    def _format_task_reward(self, definition: SectTaskDefinition) -> str:
        parts: list[str] = []
        if definition.reward_lingshi > 0:
            parts.append(f"灵石 +{definition.reward_lingshi}")
        if definition.reward_soul > 0:
            parts.append(f"器魂 +{definition.reward_soul}")
        if definition.reward_luck > 0:
            parts.append(f"气运 +{definition.reward_luck}")
        return " · ".join(parts)

    def _build_attack_summaries(self, progress_map: dict[int, int], required_progress: int, sect_names: dict[int, str]) -> tuple[str, ...]:
        ordered = sorted(progress_map.items(), key=lambda item: (-item[1], item[0]))
        result = []
        for sect_id, progress in ordered[:2]:
            name = sect_names.get(sect_id, f"宗门{sect_id}")
            result.append(f"{name} {progress}/{required_progress}")
        return tuple(result)

    def _roll_site_name(self, site_type: str, used_names: set[str]) -> str:
        candidates = [name for name in SITE_NAME_POOLS[site_type] if name not in used_names]
        if not candidates:
            candidates = list(SITE_NAME_POOLS[site_type])
        return self.rng.choice(candidates)

    def _site_is_active(self, site: WorldResourceSite, today) -> bool:
        if site.expires_on is None:
            return True
        return site.expires_on >= today

    def _remove_member_from_state(self, state: dict[str, object], character_id: int) -> bool:
        changed = False
        if character_id in state["guard_member_ids"]:
            state["guard_member_ids"] = [member_id for member_id in state["guard_member_ids"] if member_id != character_id]
            changed = True
        if character_id in state["transport_member_ids"]:
            state["transport_member_ids"] = [member_id for member_id in state["transport_member_ids"] if member_id != character_id]
            changed = True
        updated_attack_members = {}
        for sect_id, member_ids in state["attack_members"].items():
            filtered = [member_id for member_id in member_ids if member_id != character_id]
            if len(filtered) != len(member_ids):
                changed = True
            if filtered:
                updated_attack_members[sect_id] = filtered
        state["attack_members"] = updated_attack_members
        return changed

    def _add_guard_member(self, state: dict[str, object], character_id: int) -> None:
        if character_id not in state["guard_member_ids"]:
            state["guard_member_ids"].append(character_id)

    def _add_transport_member(self, state: dict[str, object], character_id: int) -> None:
        if character_id not in state["transport_member_ids"]:
            state["transport_member_ids"].append(character_id)

    async def _clear_bound_characters(self, session: AsyncSession, member_ids: set[int] | list[int], site_id: int) -> None:
        ids = self._dedupe_ids(member_ids)
        if not ids:
            return
        characters = list((await session.scalars(select(Character).where(Character.id.in_(ids)))).all())
        for character in characters:
            if character.sect_bound_site_id == site_id:
                character.sect_bound_site_id = None
                character.sect_bound_site_role = None

    async def _set_bound_characters(self, session: AsyncSession, member_ids: list[int], site_id: int, role: str) -> None:
        ids = self._dedupe_ids(member_ids)
        if not ids:
            return
        characters = list((await session.scalars(select(Character).where(Character.id.in_(ids)))).all())
        for character in characters:
            character.sect_bound_site_id = site_id
            character.sect_bound_site_role = role

    def _collect_state_member_ids(self, state: dict[str, object]) -> set[int]:
        member_ids = set(state["guard_member_ids"]) | set(state["transport_member_ids"])
        for ids in state["attack_members"].values():
            member_ids.update(ids)
        return member_ids

    def _dedupe_ids(self, member_ids) -> list[int]:
        seen: set[int] = set()
        result: list[int] = []
        for raw_member_id in member_ids:
            member_id = int(raw_member_id)
            if member_id in seen:
                continue
            seen.add(member_id)
            result.append(member_id)
        return result

    @staticmethod
    def _empty_state() -> dict[str, object]:
        return {
            "attack_progress": {},
            "attack_members": {},
            "guard_member_ids": [],
            "transport_member_ids": [],
        }

    @staticmethod
    def _load_state(raw_json: str | None) -> dict[str, object]:
        state = SectService._empty_state()
        if not raw_json:
            return state
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return state
        if not isinstance(payload, dict):
            return state

        raw_progress = payload.get("attack_progress", {})
        if isinstance(raw_progress, dict):
            state["attack_progress"] = {
                int(key): int(value)
                for key, value in raw_progress.items()
                if isinstance(value, int) and int(value) > 0
            }

        raw_attack_members = payload.get("attack_members", {})
        if isinstance(raw_attack_members, dict):
            attack_members: dict[int, list[int]] = {}
            for key, value in raw_attack_members.items():
                if not isinstance(value, list):
                    continue
                attack_members[int(key)] = [int(member_id) for member_id in value if isinstance(member_id, int)]
            state["attack_members"] = attack_members

        raw_guard_ids = payload.get("guard_member_ids", [])
        if isinstance(raw_guard_ids, list):
            state["guard_member_ids"] = [int(member_id) for member_id in raw_guard_ids if isinstance(member_id, int)]

        raw_transport_ids = payload.get("transport_member_ids", [])
        if isinstance(raw_transport_ids, list):
            state["transport_member_ids"] = [int(member_id) for member_id in raw_transport_ids if isinstance(member_id, int)]

        return state

    @staticmethod
    def _dump_state(payload: dict[str, object]) -> str:
        normalized = {
            "attack_progress": {str(key): int(value) for key, value in payload.get("attack_progress", {}).items()},
            "attack_members": {
                str(key): [int(member_id) for member_id in value]
                for key, value in payload.get("attack_members", {}).items()
            },
            "guard_member_ids": [int(member_id) for member_id in payload.get("guard_member_ids", [])],
            "transport_member_ids": [int(member_id) for member_id in payload.get("transport_member_ids", [])],
        }
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    def _random_int(self, start: int, end: int) -> int:
        return self.rng.randint(start, end)
