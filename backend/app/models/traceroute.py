"""ORM-Modelle für Traceroute / Pfad-Analyse.

Ein ``TracerouteTarget`` ist ein konfiguriertes Ziel (wie beim Dauerping). Ein
``Traceroute`` ist ein einzelner Lauf mit den zugehörigen ``TracerouteHop``-Zeilen.
Wie beim ``Report`` gibt es **keinen Fremdschlüssel** auf das Ziel – ein Lauf
bleibt auch nach dem Löschen des Ziels auswertbar (``target_id`` nur als lose
Referenz).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class TracerouteTarget(Base):
    __tablename__ = "traceroute_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    # "icmp" | "udp" | "tcp"
    mode: Mapped[str] = mapped_column(String(8), default="icmp", nullable=False)
    tcp_port: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    max_hops: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    queries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    resolve_geo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)


class Traceroute(Base):
    __tablename__ = "traceroutes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    # Lose Referenz (kein FK): überlebt das Löschen des Ziels.
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="icmp", nullable=False)
    engine: Mapped[str] = mapped_column(String(16), default="mtr", nullable=False)

    total_hops: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dest_reached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    hops: Mapped[list[TracerouteHop]] = relationship(
        back_populates="traceroute", cascade="all, delete-orphan", order_by="TracerouteHop.hop_number"
    )


class TracerouteHop(Base):
    __tablename__ = "traceroute_hops"

    id: Mapped[int] = mapped_column(primary_key=True)
    traceroute_id: Mapped[int] = mapped_column(
        ForeignKey("traceroutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    hop_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_min_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_avg_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_max_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Geo/ASN (best effort)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    traceroute: Mapped[Traceroute] = relationship(back_populates="hops")


class GeoipCache(Base):
    """Cache für Geo-/ASN-Auflösungen (lokale MaxMind-DB **oder** ip-api.com)."""

    __tablename__ = "geoip_cache"

    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    asn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # "mmdb" | "ip-api"
    source: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    fetched_at: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
