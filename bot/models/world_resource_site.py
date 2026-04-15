from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin


class WorldResourceSite(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "world_resource_sites"

    site_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    site_name: Mapped[str] = mapped_column(String(64))
    site_type: Mapped[str] = mapped_column(String(16))
    owner_sect_id: Mapped[int | None] = mapped_column(ForeignKey("sects.id", ondelete="SET NULL"), nullable=True)
    settlement_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    state_json: Mapped[str] = mapped_column(Text, default="")

    owner_sect = relationship("Sect", back_populates="resource_sites")
