"""SQLAlchemy Declarative Base."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator


class UTCDateTime(TypeDecorator):
    """Speichert Zeitstempel immer als naive UTC-Werte und liefert sie als
    timezone-aware UTC zurück. So bleibt der interne Standard konsistent UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            from datetime import UTC

            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        from datetime import UTC

        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


def utc_column(**kwargs):
    return mapped_column(UTCDateTime, **kwargs)
