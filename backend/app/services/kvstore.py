"""Zugriff auf den AppSetting-Key/Value-Store und einen DB-basierten Job-Lock."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.timeutils import utcnow
from app.models.settings import AppSetting


def get_value(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(AppSetting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return default


def set_value(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    payload = json.dumps(value)
    if row is None:
        db.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload
    db.flush()


def acquire_lock(db: Session, key: str, ttl_s: int = 3600) -> bool:
    """Versucht, einen Lock zu setzen. Gibt True bei Erfolg zurück.
    Abgelaufene Locks (älter als ttl_s) werden überschrieben."""
    now = utcnow().timestamp()
    current = get_value(db, key, None)
    if current is not None and (now - float(current)) < ttl_s:
        return False
    set_value(db, key, now)
    db.commit()
    return True


def release_lock(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.commit()


def is_locked(db: Session, key: str, ttl_s: int = 3600) -> bool:
    current = get_value(db, key, None)
    if current is None:
        return False
    return (utcnow().timestamp() - float(current)) < ttl_s
