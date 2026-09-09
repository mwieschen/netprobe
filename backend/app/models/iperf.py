from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class IperfResult(Base):
    """Ergebnis eines iperf3-Client-Laufs."""

    __tablename__ = "iperf_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5201)
    protocol: Mapped[str] = mapped_column(String(8), nullable=False, default="tcp")
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reverse: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    recv_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    lost_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    lost_packets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    out_of_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retransmits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
