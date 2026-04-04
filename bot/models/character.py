from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String
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
    last_idle_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_qi_recovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fate_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(64), default="未立尊号")
    combat_power: Mapped[int] = mapped_column(BigInteger, default=0)
    best_ladder_rank: Mapped[int] = mapped_column(Integer, default=1)
    current_ladder_rank: Mapped[int] = mapped_column(Integer, default=1, index=True)
    daily_pvp_attempts_used: Mapped[int] = mapped_column(Integer, default=0)
    last_pvp_reset_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reincarnation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reincarnated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_highlight_text: Mapped[str] = mapped_column(String(255), default="初入仙途，灵台未染。")

    player = relationship("Player", back_populates="character")
    artifact = relationship("Artifact", back_populates="character", uselist=False, cascade="all, delete-orphan")
    ladder_record = relationship("LadderRecord", back_populates="character", uselist=False, cascade="all, delete-orphan")
