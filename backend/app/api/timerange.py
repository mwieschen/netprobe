"""Gemeinsame Zeitraum-Auflösung und Ausdünnung für Verlaufs-/Diagramm-Endpunkte.

Alle Verlaufs-Endpunkte akzeptieren entweder ein Preset (``range=24h`` …) oder einen
absoluten Zeitraum (``start``/``end`` als ISO-8601). So kann die Oberfläche auch in
ältere Aufzeichnungen zurückblättern statt nur "die letzten X Stunden" zu zeigen.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.timeutils import utcnow

RANGE_PRESETS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "365d": timedelta(days=365),
}


def _parse_ts(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            422, f"Ungültiger Zeitstempel für '{field}'"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def resolve_range(
    range: str | None,
    start: str | None = None,
    end: str | None = None,
    default: str = "24h",
) -> tuple[datetime | None, datetime | None]:
    """Liefert ``(since, until)`` in UTC. ``None`` bedeutet "unbegrenzt".

    ``start``/``end`` haben Vorrang vor ``range``. Fehlt ``end``, gilt "jetzt"
    (bzw. unbegrenzt). Unbekannte Presets fallen auf ``default`` zurück.
    """
    if start or end:
        since = _parse_ts(start, "start") if start else None
        until = _parse_ts(end, "end") if end else None
        if since is not None and until is not None and since >= until:
            raise HTTPException(422, "'start' muss vor 'end' liegen")
        return since, until
    if range == "all":
        return None, None
    delta = RANGE_PRESETS.get(range or default) or RANGE_PRESETS[default]
    return utcnow() - delta, None


def apply_range(q, ts_col, since: datetime | None, until: datetime | None):
    if since is not None:
        q = q.where(ts_col >= since)
    if until is not None:
        q = q.where(ts_col <= until)
    return q


def thinned_rows(db: Session, model: Any, conditions: list, ts_col, limit: int) -> list:
    """Alle Zeilen im Zeitraum, bei mehr als ``limit`` Treffern gleichmäßig ausgedünnt
    (jede n-te Zeile, erste und letzte bleiben erhalten). Sortierung: neueste zuerst.

    Vorher wurden einfach die neuesten ``limit`` Zeilen geliefert – bei großen
    Zeiträumen zeigte das Diagramm dadurch nur die letzten Stunden statt des
    gewählten Zeitraums.
    """
    total = db.execute(select(func.count()).select_from(model).where(*conditions)).scalar() or 0
    if total <= limit:
        q = select(model).where(*conditions).order_by(ts_col.desc())
        return list(db.execute(q).scalars().all())
    # Ausdünnung komplett in SQL (ROW_NUMBER), damit nie mehr als ~limit Zeilen
    # bzw. IDs aus der Datenbank geladen werden – auch nicht bei range=all.
    stride = math.ceil(total / limit)
    rn = func.row_number().over(order_by=ts_col).label("rn")
    sub = select(model.id.label("id"), rn).where(*conditions).subquery()
    q = (
        select(model)
        .join(sub, model.id == sub.c.id)
        .where(or_((sub.c.rn - 1) % stride == 0, sub.c.rn == total))
        .order_by(ts_col.desc())
    )
    return list(db.execute(q).scalars().all())
