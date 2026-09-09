"""ORM-Modelle für das VoIP-/3CX-Verbindungsmonitoring.

Ein ``VoipServer`` ist eine konfigurierte 3CX-Telefonanlage (oder ein anderer
SIP-Endpunkt). Ein ``VoipTest`` ist das Aggregat eines Testlaufs über alle
Teilprüfungen (SIP-Ports, SIP-over-TLS, WebRTC-Ports, ICMP-Qualität, optional
Registrierung). SIP-Zugangsdaten für den optionalen REGISTER-Test werden
**nicht** gespeichert – sie kommen ausschließlich transient im Request-Body.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class VoipServer(Base):
    __tablename__ = "voip_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    sip_port: Mapped[int] = mapped_column(Integer, default=5060, nullable=False)
    sip_tls_port: Mapped[int] = mapped_column(Integer, default=5061, nullable=False)
    # Kommaliste TCP-Ports für den WebRTC-/HTTPS-Erreichbarkeitstest.
    webrtc_ports: Mapped[str] = mapped_column(String(64), default="443,5001", nullable=False)
    # Transport für den SIP-OPTIONS-Ping: "udp" oder "tcp".
    sip_transport: Mapped[str] = mapped_column(String(8), default="udp", nullable=False)
    check_registration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)

    tests: Mapped[list[VoipTest]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )


class VoipTest(Base):
    __tablename__ = "voip_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("voip_servers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)

    # None = nicht geprüft, True/False = Ergebnis der Teilprüfung.
    sip_udp_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sip_tcp_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sip_tls_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # JSON-Liste: [{"port": 443, "ok": true, "ms": 12.3}, ...]
    webrtc_results_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    sip_response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    sip_response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tls_handshake_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tls_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tls_cert_expiry: Mapped[datetime | None] = utc_column(nullable=True)

    loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_avg_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    registration_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    server: Mapped[VoipServer] = relationship(back_populates="tests")
