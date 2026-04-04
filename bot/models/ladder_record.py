from __future__ import annotations

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin


class LadderRecord(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "ladder_records"

    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), unique=True, index=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    streak: Mapped[int] = mapped_column(Integer, default=0)

    character = relationship("Character", back_populates="ladder_record")
