from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class MtuTest(Base):
    """Ergebnis einer Pfad-MTU-Ermittlung zu einem Ziel."""

    __tablename__ = "mtu_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    path_mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_mtu: Mapped[int] = mapped_column(Integer, nullable=False, default=68)
    max_mtu: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    probe_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
