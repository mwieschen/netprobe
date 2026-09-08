from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class PingJob(Base):
    __tablename__ = "ping_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # "running", "paused", "stopped"
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)

    samples: Mapped[list[PingSample]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class PingSample(Base):
    """Rohdaten eines einzelnen Pings (14 Tage Aufbewahrung)."""

    __tablename__ = "ping_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ping_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    # rtt_ms = None bedeutet Timeout/Verlust
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    job: Mapped[PingJob] = relationship(back_populates="samples")


class PingAggregate(Base):
    """5-Minuten-Aggregate (langfristige Aufbewahrung)."""

    __tablename__ = "ping_aggregates"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ping_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    bucket_ts: Mapped[datetime] = utc_column(index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    loss: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rtt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
