from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin


class Player(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "players"

    discord_user_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))

    character = relationship("Character", back_populates="player", uselist=False, cascade="all, delete-orphan")
