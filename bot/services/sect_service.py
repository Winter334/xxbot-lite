from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import random
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.data.realms import get_stage
from bot.data.resource_sites import RESOURCE_SITE_DEFINITIONS, SITE_TYPE_NAMES
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
SITE_ACTION_LABELS = {
    "contest": "强争",
    "guard": "护持",
    "transport": "输运",
}
SITE_REWARD_CONFIG = {
    "lingshi": {"lingshi": 200, "soul": 10},
    "soul": {"lingshi": 120, "soul": 18},
    "cultivation": {"lingshi": 100, "cultivation_pct": 5},
}
SITE_FIXED_CULTIVATION = {
    "lianqi": 10,
    "zhuji": 40,
    "jiedan": 200,
    "yuanying": 1000,
    "huashen": 5000,
    "lianxu": 20000,
    "heti": 80000,
    "dacheng": 200000,
    "dujie": 400000,
}


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


@dataclass(slots=True)
class SectActionResult:
    success: bool
    message: str
    contribution_gain: int = 0
    qi_before: int = 0
    qi_after: int = 0
    site_name: str = ""
    site_type_name: str = ""


class SectService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    async def ensure_sites(self, session: AsyncSession) -> None:
        existing = {
            site.site_key: site
            for site in (
                await session.scalars(select(WorldResourceSite))
            ).all()
        }
        created = False
        for definition in RESOURCE_SITE_DEFINITIONS:
            if definition.site_key in existing:
                continue
            session.add(
                WorldResourceSite(
                    site_key=definition.site_key,
                    site_name=definition.site_name,
                    site_type=definition.site_type,
                    owner_sect_id=None,
                    settlement_day=today_shanghai(),
                    state_json=self._dump_state({}),
                )
            )
            created = True
        if created:
            await session.flush()

    async def settle_sites_if_needed(self, session: AsyncSession) -> dict[int, list[str]]:
        await self.ensure_sites(session)
        sites = list(
            (
                await session.scalars(
                    select(WorldResourceSite).options(selectinload(WorldResourceSite.owner_sect))
                )
            ).all()
        )
        today = today_shanghai()
        if not sites:
            return {}
        if all(site.settlement_day == today for site in sites if site.settlement_day is not None):
            await self._sync_character_contribution_dates(session, today)
            return {}

        characters = list(
            (
                await session.scalars(
                    select(Character).options(selectinload(Character.player), selectinload(Character.artifact), selectinload(Character.sect))
                )
            ).all()
        )
        notices: dict[int, list[str]] = {}
        for site in sites:
            if site.settlement_day is None:
                site.settlement_day = today
                if not site.state_json:
                    site.state_json = self._dump_state({})
                continue
            if site.settlement_day >= today:
                continue
            previous_day = site.settlement_day
            state = self._load_state(site.state_json)
            sect_scores = state.get("sect_scores", {})
            if sect_scores:
                winner_sect_id = max(sect_scores.items(), key=lambda item: (item[1], -item[0]))[0]
                site.owner_sect_id = winner_sect_id
            winner_sect_id = site.owner_sect_id
            if winner_sect_id is not None:
                for character in characters:
                    if character.sect_id != winner_sect_id:
                        continue
                    if character.sect_last_contribution_on != previous_day:
                        continue
                    if (character.sect_contribution_daily or 0) <= 0:
                        continue
                    if character.sect_joined_at is None or ensure_shanghai(character.sect_joined_at).date() > previous_day:
                        continue
                    reward_lines = self._apply_site_reward(character, site)
                    if reward_lines:
                        notices.setdefault(character.id, []).extend(reward_lines)
            site.settlement_day = today
            site.state_json = self._dump_state({})

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
        for sect in sects:
            if len(sect.members) >= SECT_MEMBER_LIMIT:
                continue
            if character.faction != "neutral" and sect.faction_key != character.faction:
                continue
            result.append(
                SectSummary(
                    sect_id=sect.id,
                    name=sect.name,
                    faction_name=FACTION_NAMES.get(sect.faction_key, "中立"),
                    member_count=len(sect.members),
                    owner_site_count=sum(1 for site in sect.resource_sites if site.owner_sect_id == sect.id),
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
        owned_sites = tuple(site.site_name for site in sect.resource_sites if site.owner_sect_id == sect.id)
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
            return True, "旧门已散，你自行抽身而出。"
        leaving_name = sect.name
        character.sect = None
        character.sect_id = None
        character.sect_joined_at = None
        character.sect_last_left_at = now_shanghai()
        character.sect_contribution_daily = 0
        character.sect_contribution_weekly = 0
        character.sect_last_contribution_on = None
        await session.flush()
        remaining_members = list(
            (
                await session.scalars(select(Character).where(Character.sect_id == sect.id))
            ).all()
        )
        if not remaining_members:
            for site in sect.resource_sites:
                site.owner_sect_id = None
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

    async def list_sites(self, session: AsyncSession) -> list[ResourceSiteView]:
        await self.ensure_sites(session)
        sites = list(
            (
                await session.scalars(
                    select(WorldResourceSite).options(selectinload(WorldResourceSite.owner_sect)).order_by(WorldResourceSite.id.asc())
                )
            ).all()
        )
        views = []
        for site in sites:
            owner_name = site.owner_sect.name if site.owner_sect is not None else "无主"
            views.append(ResourceSiteView(site.id, site.site_name, SITE_TYPE_NAMES.get(site.site_type, site.site_type), owner_name))
        return views

    async def perform_site_action(self, session: AsyncSession, character: Character, site_id: int, action_key: str) -> SectActionResult:
        if character.sect_id is None:
            return SectActionResult(False, "你尚未归于任何宗门。")
        if action_key not in SITE_ACTION_LABELS:
            return SectActionResult(False, "此番行动未能立意。")
        if character.current_qi < SECT_ACTION_QI_COST:
            return SectActionResult(False, "气机不足，难以再为宗门奔走。", qi_before=character.current_qi, qi_after=character.current_qi)
        site = await session.scalar(
            select(WorldResourceSite).where(WorldResourceSite.id == site_id).options(selectinload(WorldResourceSite.owner_sect))
        )
        if site is None:
            return SectActionResult(False, "此处资源点气机已散。")

        today = today_shanghai()
        self._sync_single_character_contribution(character, today)
        qi_before = character.current_qi
        character.current_qi -= SECT_ACTION_QI_COST
        contribution = self._calculate_contribution(character, action_key, owner_bonus=(site.owner_sect_id == character.sect_id and action_key == "guard"))
        character.sect_contribution_total += contribution
        character.sect_contribution_weekly += contribution
        character.sect_contribution_daily += contribution
        character.sect_last_contribution_on = today

        state = self._load_state(site.state_json)
        sect_scores = state.setdefault("sect_scores", {})
        sect_scores[str(character.sect_id)] = int(sect_scores.get(str(character.sect_id), 0)) + contribution
        site.state_json = self._dump_state(state)
        character.last_highlight_text = f"方才为宗门奔赴 {site.site_name}，留下 {contribution} 点功绩。"
        return SectActionResult(
            True,
            f"你已于 {site.site_name} 行过一番 {SITE_ACTION_LABELS[action_key]}。",
            contribution_gain=contribution,
            qi_before=qi_before,
            qi_after=character.current_qi,
            site_name=site.site_name,
            site_type_name=SITE_TYPE_NAMES.get(site.site_type, site.site_type),
        )

    def _calculate_contribution(self, character: Character, action_key: str, *, owner_bonus: bool) -> int:
        stage = get_stage(character.realm_key, character.stage_key)
        if action_key == "contest":
            value = 60 + stage.global_stage_index * 3 + self._random_int(0, 15)
        elif action_key == "guard":
            value = 50 + stage.global_stage_index * 2 + self._random_int(0, 10)
        else:
            value = 45 + self._random_int(0, 8)
        if owner_bonus:
            value = int(value * 1.2)
        return max(1, value)

    def _apply_site_reward(self, character: Character, site: WorldResourceSite) -> list[str]:
        config = SITE_REWARD_CONFIG[site.site_type]
        parts: list[str] = []
        if config.get("lingshi", 0):
            character.lingshi += config["lingshi"]
            parts.append(f"灵石 +{config['lingshi']}")
        if config.get("soul", 0) and character.artifact is not None:
            character.artifact.soul_shards += config["soul"]
            parts.append(f"器魂 +{config['soul']}")
        if config.get("cultivation_pct", 0):
            stage = get_stage(character.realm_key, character.stage_key)
            fixed_amount = SITE_FIXED_CULTIVATION.get(character.realm_key, 0)
            percent_amount = max(1, stage.cultivation_max * config["cultivation_pct"] // 100)
            total_amount = min(fixed_amount + percent_amount, max(0, stage.cultivation_max - character.cultivation))
            if total_amount > 0:
                character.cultivation += total_amount
                parts.append(f"修为 +{total_amount}")
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

    @staticmethod
    def _load_state(raw_json: str | None) -> dict[str, dict[str, int]]:
        if not raw_json:
            return {"sect_scores": {}}
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return {"sect_scores": {}}
        if not isinstance(payload, dict):
            return {"sect_scores": {}}
        raw_scores = payload.get("sect_scores")
        if not isinstance(raw_scores, dict):
            return {"sect_scores": {}}
        scores = {int(key): int(value) for key, value in raw_scores.items() if isinstance(value, int)}
        return {"sect_scores": scores}

    @staticmethod
    def _dump_state(payload: dict[str, dict[str, int]]) -> str:
        normalized = {
            "sect_scores": {str(key): value for key, value in payload.get("sect_scores", {}).items()},
        }
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    def _random_int(self, start: int, end: int) -> int:
        return self.rng.randint(start, end)
