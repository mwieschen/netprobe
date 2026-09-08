"""Zeit-Hilfsfunktionen. Intern wird ausschließlich UTC verwendet; die
Konvertierung nach Europe/Berlin erfolgt nur für die Darstellung."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

_LOCAL_TZ = ZoneInfo(settings.timezone)


def utcnow() -> datetime:
    """Aktuelle Zeit als timezone-aware UTC."""
    return datetime.now(UTC)


def to_local(dt: datetime) -> datetime:
    """UTC -> lokale Zeitzone (Europe/Berlin)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_LOCAL_TZ)


def isoformat_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()
