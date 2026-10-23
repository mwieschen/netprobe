"""Wachtl-Clients: Standorte, Probes, Checks, Enrollment-Token und verdichtete Messwerte.

netprobe ist hier Zentrale für unveränderte Wachtl-Windows-Probes (Probe-API v1, siehe
``app.api.wachtl_probe`` und ``docs/wachtl-zentrale.md``). Rohwerte werden nicht gespeichert,
sondern beim Ingest zu 1-Minuten-Buckets verdichtet (``WachtlSample``, ``step_s=60``) und
später zu Stunden-Buckets (``step_s=3600``) zusammengefasst.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class WachtlSite(Base):
    __tablename__ = "wachtl_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)


class WachtlEnrollToken(Base):
    """Enrollment-Token für die Installation (Klartext nur einmal bei der Erzeugung sichtbar)."""

    __tablename__ = "wachtl_enroll_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_sites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = utc_column(nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)


class WachtlProbe(Base):
    __tablename__ = "wachtl_probes"
    __table_args__ = (UniqueConstraint("site_id", "hostname", name="uq_wachtl_probes_site_host"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_sites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    hostname: Mapped[str] = mapped_column(String(64), nullable=False)
    os: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # SHA-256 des Tokens "<probe_id>.<secret>"; nach Abmeldung ein unbrauchbarer Platzhalter.
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # "active" oder "disabled"
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)
    last_seen: Mapped[datetime | None] = utc_column(nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    buffer_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uptime_s: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    addresses_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    deregistered_at: Mapped[datetime | None] = utc_column(nullable=True)


class WachtlCheck(Base):
    """Messauftrag für Probes. ``site_id`` NULL = alle Standorte."""

    __tablename__ = "wachtl_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="ping", nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    # "target", "gateway" oder "reference" (wie in Wachtl)
    role: Mapped[str] = mapped_column(String(16), default="target", nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("wachtl_sites.id", ondelete="CASCADE"), index=True, nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)


class WachtlIngestBatch(Base):
    """Bereits verarbeitete Upload-Batches (Wiederholungen nach Verbindungsabbruch sind idempotent)."""

    __tablename__ = "wachtl_ingest_batches"

    batch_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_probes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    received_at: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)


class WachtlSample(Base):
    """Verdichtete Ping-Messwerte je Probe, Check und Bucket (60 s oder 3600 s)."""

    __tablename__ = "wachtl_samples"
    __table_args__ = (
        UniqueConstraint("probe_id", "check_id", "step_s", "bucket_ts", name="uq_wachtl_samples_bucket"),
        Index("ix_wachtl_samples_series", "probe_id", "check_id", "bucket_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_probes.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_checks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    bucket_ts: Mapped[datetime] = utc_column(index=True, nullable=False)
    step_s: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    loss: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rtt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rtt_n: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jitter_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    jitter_n: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WachtlReport(Base):
    """Störungsmeldung eines Benutzers (Tray-Symbol „Problem melden“), idempotent je (Probe, report_id)."""

    __tablename__ = "wachtl_reports"
    __table_args__ = (UniqueConstraint("probe_id", "report_id", name="uq_wachtl_reports_probe_report"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_probes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_sites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    report_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = utc_column(index=True, nullable=False)
    received_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    duration: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    # "open" oder "done"
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    done_at: Mapped[datetime | None] = utc_column(nullable=True)
    # Automatisch oder manuell gestarteter netprobe-Diagnose-Lauf (ohne FK: Lauf kann gelöscht werden)
    diagnostic_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WachtlFinding(Base):
    """Client-Hinweis einer Probe (Kabel getrennt, Link-Geschwindigkeit, Treiber …), Upsert je finding_id."""

    __tablename__ = "wachtl_findings"
    __table_args__ = (UniqueConstraint("probe_id", "finding_id", name="uq_wachtl_findings_probe_finding"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_probes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("wachtl_sites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    finding_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # "active" oder "resolved"
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adapter_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    detected_at: Mapped[datetime] = utc_column(index=True, nullable=False)
    resolved_at: Mapped[datetime | None] = utc_column(nullable=True)
    received_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)
