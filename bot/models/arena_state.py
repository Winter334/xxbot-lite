from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, IdentityMixin, TimestampMixin


class ArenaState(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "arena_states"

    arena_key: Mapped[str] = mapped_column(String(32), unique=True, index=True, default="public")
    champion_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    stake_soul: Mapped[int] = mapped_column(BigInteger, default=0)
    pot_soul: Mapped[int] = mapped_column(BigInteger, default=0)
    win_streak: Mapped[int] = mapped_column(Integer, default=0)

    champion = relationship("Character", foreign_keys=[champion_character_id])
