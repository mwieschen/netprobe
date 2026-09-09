"""MTU-Test-Endpunkte: Pfad-MTU zu einem Ziel ermitteln, Verlauf abrufen."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db
from app.models.mtu import MtuTest
from app.models.user import User
from app.schemas.schemas import MtuTestOut, MtuTestRequest, MtuTestResult
from app.services import audit, mtu_service

router = APIRouter(prefix="/api/mtu-test", tags=["mtu"])


@router.get("/results", response_model=list[MtuTestOut])
def list_results(
    limit: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[MtuTest]:
    return list(
        db.execute(
            select(MtuTest).order_by(MtuTest.ts.desc()).limit(min(limit, 500))
        ).scalars().all()
    )


@router.post("", response_model=MtuTestResult, status_code=status.HTTP_201_CREATED)
async def run_mtu_test(
    payload: MtuTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> MtuTestResult:
    try:
        data = await asyncio.to_thread(
            mtu_service.discover_path_mtu, payload.target, payload.min_mtu, payload.max_mtu
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    row = MtuTest(
        target=data["target"],
        ok=data["ok"],
        path_mtu=data["path_mtu"],
        min_mtu=data["min_mtu"],
        max_mtu=data["max_mtu"],
        probe_count=data["probe_count"],
        error=data["error"],
    )
    db.add(row)
    audit.record(db, "mtu_test", user_id=user.id, username=user.username,
                 detail=f"{data['target']} -> {data['path_mtu']}", ip=client_ip(request))
    db.commit()
    db.refresh(row)

    return MtuTestResult(
        id=row.id,
        ts=row.ts,
        target=row.target,
        ok=row.ok,
        path_mtu=row.path_mtu,
        min_mtu=row.min_mtu,
        max_mtu=row.max_mtu,
        probe_count=row.probe_count,
        error=row.error,
        probes=data["probes"],
    )
