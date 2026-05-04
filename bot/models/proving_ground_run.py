from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, IdentityMixin, TimestampMixin


class ProvingGroundRun(Base, IdentityMixin, TimestampMixin):
    __tablename__ = "proving_ground_runs"

    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    status: Mapped[str] = mapped_column(default="running")  # running / completed / failed / expired
    map_json: Mapped[str] = mapped_column(Text, default="{}")
    current_node_id: Mapped[int] = mapped_column(Integer, default=0)
    build_json: Mapped[str] = mapped_column(Text, default="{}")
    pending_affix_ops: Mapped[int] = mapped_column(Integer, default=0)
    pending_spirit_ops: Mapped[int] = mapped_column(Integer, default=0)
    boss_type: Mapped[str] = mapped_column(default="")
    boss_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    score: Mapped[int] = mapped_column(Integer, default=0)
    lingshi_invested: Mapped[int] = mapped_column(BigInteger, default=0)
    last_action_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
