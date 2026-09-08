from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class Capture(Base):
    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    iface: Mapped[str] = mapped_column(String(64), nullable=False)
    # "running", "done", "error"
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    max_packets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_filesize_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ringbuffer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bpf: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    packet_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
