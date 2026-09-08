"""Audit-Log-Abfrage (nur admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.schemas import AuditOut

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditOut])
def list_audit(
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[AuditLog]:
    return list(
        db.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(min(limit, 1000)))
        .scalars()
        .all()
    )
