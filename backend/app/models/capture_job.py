from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutils import utcnow
from app.db.base import Base, utc_column


class CaptureJob(Base):
    """Langzeit-Capture mit Ringpuffer (dumpcap läuft dauerhaft im Hintergrund).

    Die rotierten PCAPNG-Dateien liegen unter
    ``<capture_dir>/<job_id>/`` und werden von dumpcap selbst begrenzt
    (``-b filesize`` / ``-b files``). Die einzelnen Dateien werden nicht in der
    DB gespiegelt, sondern bei Bedarf aus dem Verzeichnis gelesen.
    """

    __tablename__ = "capture_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    iface: Mapped[str] = mapped_column(String(64), nullable=False)
    bpf: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_file_size_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_files: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    # "running", "stopped", "error"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[datetime] = utc_column(default=utcnow, index=True, nullable=False)
    stopped_at: Mapped[datetime | None] = utc_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
