from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class Report(Base):
    """Erzeugter Kunden-/Kollegen-Report (HTML, optional PDF).

    Die Dateien liegen unter ``<reports_dir>/<id>.html`` bzw. ``.pdf``. Es gibt
    bewusst keine Fremdschlüssel auf Capture/CaptureJob – ein Report bleibt auch
    dann gültig, wenn die zugrunde liegende Aufnahme später gelöscht wird.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    capture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-Liste der einbezogenen Abschnitte
    sections: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON-Snapshot aller aggregierten Kennzahlen (Key Metrics, aktive Tests,
    # passive Analyse, Interpretationen) – macht den Report reproduzierbar und
    # erlaubt GET /api/reports/<id> ohne erneuten tshark-Lauf.
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    has_pdf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
