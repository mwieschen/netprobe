from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class SpeedtestResult(Base):
    __tablename__ = "speedtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    download_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    upload_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ping_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    server_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "manual" oder "scheduled"
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
