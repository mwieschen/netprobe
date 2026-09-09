"""ORM-Modelle für die System-Health-Checks (DNS, HTTP(S), SMTP, IMAP, NTP).

Eine ``HealthProbe`` ist eine konfigurierte Prüfung mit typ­spezifischen Parametern
(``params_json``). Ein ``HealthCheckResult`` ist ein Lauf.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column

CHECK_TYPES = ("dns", "http", "smtp", "imap", "ntp")


class HealthProbe(Base):
    __tablename__ = "health_probes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "dns" | "http" | "smtp" | "imap" | "ntp"
    check_type: Mapped[str] = mapped_column(String(8), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    # Typ-spezifische Parameter als JSON-Objekt (siehe health-Framework).
    params_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)

    results: Mapped[list[HealthCheckResult]] = relationship(
        back_populates="probe", cascade="all, delete-orphan"
    )


class HealthCheckResult(Base):
    __tablename__ = "health_check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("health_probes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kurzstatus, z. B. "200", "NOERROR", "STARTTLS", "offset +12ms"
    status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-Objekt mit Detailinfos (Records, Cert-Ablauf, Stratum, …)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    probe: Mapped[HealthProbe] = relationship(back_populates="results")
