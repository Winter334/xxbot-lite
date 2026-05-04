from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin, utcnow


class Character(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "characters"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), unique=True, index=True)
    realm_key: Mapped[str] = mapped_column(String(16), default="lianqi")
    realm_index: Mapped[int] = mapped_column(Integer, default=1)
    stage_key: Mapped[str] = mapped_column(String(16), default="early")
    stage_index: Mapped[int] = mapped_column(Integer, default=1)
    cultivation: Mapped[int] = mapped_column(BigInteger, default=0)
    highest_floor: Mapped[int] = mapped_column(Integer, default=0)
    historical_highest_floor: Mapped[int] = mapped_column(Integer, default=0)
    current_qi: Mapped[int] = mapped_column(Integer, default=6)
    qi_max: Mapped[int] = mapped_column(Integer, default=6)
    is_retreating: Mapped[bool] = mapped_column(Boolean, default=False)
    retreat_mode: Mapped[str] = mapped_column(String(16), default="cultivation")
    is_traveling: Mapped[bool] = mapped_column(Boolean, default=False)
    last_idle_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    travel_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    travel_duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    travel_selected_duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    travel_atk_pct: Mapped[int] = mapped_column(Integer, default=0)
    travel_def_pct: Mapped[int] = mapped_column(Integer, default=0)
    travel_agi_pct: Mapped[int] = mapped_column(Integer, default=0)
    last_qi_recovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sect_id: Mapped[int | None] = mapped_column(ForeignKey("sects.id", ondelete="SET NULL"), nullable=True, index=True)
    sect_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sect_last_left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sect_contribution_total: Mapped[int] = mapped_column(BigInteger, default=0)
    sect_contribution_weekly: Mapped[int] = mapped_column(BigInteger, default=0)
    sect_contribution_daily: Mapped[int] = mapped_column(BigInteger, default=0)
    sect_last_contribution_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sect_bound_site_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sect_bound_site_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sect_last_settlement_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sect_last_settlement_summary: Mapped[str] = mapped_column(Text, default="")
    sect_task_refresh_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sect_task_state_json: Mapped[str] = mapped_column(Text, default="")
    lingshi: Mapped[int] = mapped_column(BigInteger, default=0)
    fate_key: Mapped[str] = mapped_column(String(64))
    honor_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    faction: Mapped[str] = mapped_column(String(16), default="neutral")
    virtue: Mapped[int] = mapped_column(BigInteger, default=0)
    infamy: Mapped[int] = mapped_column(BigInteger, default=0)
    luck: Mapped[int] = mapped_column(BigInteger, default=0)
    bounty_soul: Mapped[int] = mapped_column(BigInteger, default=0)
    last_bounty_growth_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_robbery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bounty_hunt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bounty_defeated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(64), default="未立尊号")
    combat_power: Mapped[int] = mapped_column(BigInteger, default=0)
    best_ladder_rank: Mapped[int] = mapped_column(Integer, default=1)
    current_ladder_rank: Mapped[int] = mapped_column(Integer, default=1, index=True)
    daily_pvp_attempts_used: Mapped[int] = mapped_column(Integer, default=0)
    last_pvp_reset_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reincarnation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reincarnated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_highlight_text: Mapped[str] = mapped_column(String(255), default="初入仙途，灵台未染。")

    # 证道战场 (Proving Ground)
    pg_boss_kills_json: Mapped[str] = mapped_column(Text, default="[]")
    pg_total_score: Mapped[int] = mapped_column(BigInteger, default=0)
    pg_best_score: Mapped[int] = mapped_column(Integer, default=0)
    pg_completions: Mapped[int] = mapped_column(Integer, default=0)
    pg_red_dust_count: Mapped[int] = mapped_column(Integer, default=0)
    dao_traces: Mapped[int] = mapped_column(BigInteger, default=0)

    # 证道战场 -- 局外永久投资
    pg_invest_stat_level: Mapped[int] = mapped_column(Integer, default=0)
    pg_invest_affix_slots: Mapped[int] = mapped_column(Integer, default=0)
    pg_invest_spirit_unlocked: Mapped[bool] = mapped_column(Boolean, default=False)

    player = relationship("Player", back_populates="character")
    sect = relationship("Sect", back_populates="members", foreign_keys=[sect_id])
    artifact = relationship("Artifact", back_populates="character", uselist=False, cascade="all, delete-orphan")
    ladder_record = relationship("LadderRecord", back_populates="character", uselist=False, cascade="all, delete-orphan")

    def stored_honor_tags(self) -> tuple[str, ...]:
        raw = getattr(self, "honor_tags_json", "[]") or "[]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list):
            return ()
        deduped: list[str] = []
        for item in parsed:
            tag = str(item).strip()
            if tag and tag not in deduped:
                deduped.append(tag)
        return tuple(deduped)

    def set_honor_tags(self, tags: list[str] | tuple[str, ...]) -> None:
        deduped: list[str] = []
        for item in tags:
            tag = str(item).strip()
            if tag and tag not in deduped:
                deduped.append(tag)
        self.honor_tags_json = json.dumps(deduped, ensure_ascii=False)

    def add_honor_tag(self, tag: str) -> bool:
        current = list(self.stored_honor_tags())
        normalized = tag.strip()
        if not normalized or normalized in current:
            return False
        current.append(normalized)
        self.set_honor_tags(current)
        return True

    # -- 证道战场 BOSS 击败记录 helpers --

    _PG_BOSS_TYPES = ("preset", "strongest", "self")

    def stored_pg_boss_kills(self) -> list[str]:
        raw = getattr(self, "pg_boss_kills_json", "[]") or "[]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list):
            return []
        return [str(t).strip() for t in parsed if str(t).strip()]

    def set_pg_boss_kills(self, kills: list[str]) -> None:
        deduped: list[str] = []
        for k in kills:
            tag = str(k).strip()
            if tag and tag not in deduped:
                deduped.append(tag)
        self.pg_boss_kills_json = json.dumps(deduped, ensure_ascii=False)

    def add_pg_boss_kill(self, boss_type: str) -> bool:
        current = self.stored_pg_boss_kills()
        bt = boss_type.strip()
        if not bt or bt in current:
            return False
        current.append(bt)
        self.set_pg_boss_kills(current)
        return True

    def has_all_pg_boss_kills(self) -> bool:
        kills = set(self.stored_pg_boss_kills())
        return all(t in kills for t in self._PG_BOSS_TYPES)
