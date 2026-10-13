"""ORM-Modelle für TCP-Retransmission-Monitoring.

Ein ``RetransmissionTest`` ist ein Lauf einer Kurz-Aufnahme (``dumpcap``/``tshark``)
auf einem Interface – passiv (laufender Traffic) oder aktiv (gezielte TCP-Connect-
Proben zu frei definierbaren Zielen, siehe ``retransmission_service._run_active_traffic``).
``RetransmissionTarget`` listet die betroffenen Gegenstellen (IP/Port),
``RetransmissionTimelinePoint`` die zeitliche Entwicklung der Rate innerhalb des
Testfensters. ``RetransmissionTargetConfig`` sind gespeicherte Ziel-Presets
(unabhängig von einzelnen Tests, z. B. "Cloudflare DNS").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column

CLASSIFICATIONS = (
    "OK",
    "LOCAL_NETWORK_ISSUE",
    "ISP_OR_WAN_LINK_ISSUE",
    "REMOTE_SERVER_ISSUE",
    "UPLINK_CONGESTION",
    "DOWNLINK_CONGESTION",
    "UNKNOWN",
)


class RetransmissionTest(Base):
    __tablename__ = "retransmission_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    # "manual" | "scheduled"
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    interface: Mapped[str] = mapped_column(String(64), nullable=False)
    bpf: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    # "passive" (Ambient-Traffic) | "active" (gezielte TCP-Connect-Proben)
    test_mode: Mapped[str] = mapped_column(String(16), default="passive", nullable=False)
    # Nur bei test_mode="active": JSON-Liste der konfigurierten Ziele
    # [{"host": "...", "ip": "...", "port": 443}, ...] – host ist die Originaleingabe
    # (kann ein Hostname sein), ip die einmalig aufgelöste Adresse.
    target_ips: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nur bei test_mode="active": alle Ziele parallel (True) oder nacheinander (False)
    concurrent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # "running" | "done" | "error"
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    total_packets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tcp_packets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_retransmissions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retransmission_rate_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Siehe CLASSIFICATIONS
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    targets: Mapped[list[RetransmissionTarget]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
        order_by="RetransmissionTarget.retransmissions.desc()",
    )
    timeline: Mapped[list[RetransmissionTimelinePoint]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
        order_by="RetransmissionTimelinePoint.t_offset_s",
    )


class RetransmissionTarget(Base):
    __tablename__ = "retransmission_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(
        ForeignKey("retransmission_tests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    target_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retransmissions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    packets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # "upload" | "download" | "unknown"
    direction: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    first_seen: Mapped[datetime | None] = utc_column(nullable=True)
    last_seen: Mapped[datetime | None] = utc_column(nullable=True)
    # Nur bei aktiven Tests befüllt (eigene TCP-Connect-Proben zu diesem Ziel).
    connection_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_errors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_connect_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    test: Mapped[RetransmissionTest] = relationship(back_populates="targets")


class RetransmissionTimelinePoint(Base):
    __tablename__ = "retransmission_timeline"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(
        ForeignKey("retransmission_tests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    t_offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    retrans_per_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    packets_per_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    test: Mapped[RetransmissionTest] = relationship(back_populates="timeline")


class RetransmissionTargetConfig(Base):
    """Gespeichertes Ziel-Preset für aktive Tests (unabhängig von einzelnen Tests)."""

    __tablename__ = "retransmission_target_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    # Aktuell ausschließlich "tcp" (Retransmissions sind ein TCP-Konzept).
    protocol: Mapped[str] = mapped_column(String(8), default="tcp", nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)
    last_used: Mapped[datetime | None] = utc_column(nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
