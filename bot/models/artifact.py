from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import IdentityMixin, Base, TimestampMixin


class Artifact(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "artifacts"

    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    artifact_rename_used: Mapped[bool] = mapped_column(Boolean, default=False)
    reinforce_level: Mapped[int] = mapped_column(Integer, default=0)
    atk_bonus: Mapped[int] = mapped_column(BigInteger, default=0)
    def_bonus: Mapped[int] = mapped_column(BigInteger, default=0)
    agi_bonus: Mapped[int] = mapped_column(BigInteger, default=0)
    soul_shards: Mapped[int] = mapped_column(BigInteger, default=0)

    character = relationship("Character", back_populates="artifact")
