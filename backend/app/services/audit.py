"""Audit-Logging sicherheitsrelevanter Aktionen."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.audit import AuditLog

log = get_logger("netprobe.audit")


def record(
    db: Session,
    action: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    entry = AuditLog(
        action=action, user_id=user_id, username=username, detail=detail, ip=ip
    )
    db.add(entry)
    db.flush()
    log.info("AUDIT %s user=%s ip=%s detail=%s", action, username, ip, detail)
