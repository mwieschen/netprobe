from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class WanTarget(Base):
    __tablename__ = "wan_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)

    results: Mapped[list[WanResult]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )


class WanResult(Base):
    __tablename__ = "wan_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("wan_targets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_stddev: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    target: Mapped[WanTarget] = relationship(back_populates="results")
