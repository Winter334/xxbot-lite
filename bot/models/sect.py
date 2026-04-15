from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin


class Sect(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "sects"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    faction_key: Mapped[str] = mapped_column(String(16), default="neutral")
    founder_character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)

    members = relationship("Character", back_populates="sect", foreign_keys="Character.sect_id")
    resource_sites = relationship("WorldResourceSite", back_populates="owner_sect")
