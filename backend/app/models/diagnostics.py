"""ORM-Modelle für Network Diagnostics (Sammel-Test mehrerer Ziele).

Ein ``DiagnosticRun`` bündelt mehrere bestehende Tests (Ping/WAN, aktive
TCP-Retransmissions, MTU, Traceroute, Speedtest, Broadcast-Anteil), die zu
denselben Zielen parallel ausgeführt werden. Die Einzelergebnisse werden als
JSON-Snapshot gespeichert (wie bei ``Report.data_json``) statt neue Fremdschlüssel
an den bestehenden Ergebnis-Tabellen (``wan_results``, ``mtu_tests`` usw.) zu
ergänzen – die Sub-Tests laufen weiterhin über ihre eigenen Services und tauchen
dort auch in der jeweiligen Historie auf. ``DiagnosticPreset`` ist eine gespeicherte
Ziel-/Test-Auswahl für wiederkehrende Checks.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class DiagnosticPreset(Base):
    __tablename__ = "diagnostic_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON-Liste [{"name": "...", "host": "..."}]
    targets_json: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON-Objekt {"ping": true, "retrans": true, "mtu": false, ...}
    tests_json: Mapped[str] = mapped_column(Text, nullable=False)
    interface: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = utc_column(default=utcnow, nullable=False)


class DiagnosticRun(Base):
    __tablename__ = "diagnostic_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    interface: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    targets_json: Mapped[str] = mapped_column(Text, nullable=False)
    tests_json: Mapped[str] = mapped_column(Text, nullable=False)
    # "running" | "done" | "error"
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    # "OK" | "WARN" | "KRITISCH" (erst gesetzt, wenn status == "done")
    overall_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # JSON-Snapshot aller Sub-Test-Ergebnisse je Ziel + Broadcast/Speedtest (global).
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
