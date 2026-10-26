"""WAN-Qualität: Ziele verwalten, Ergebnisse abfragen, Ad-hoc-Messung."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.api.timerange import resolve_range, thinned_rows
from app.db.session import get_db, session_scope
from app.models.user import User
from app.models.wan import WanResult, WanTarget
from app.schemas.schemas import (
    WanResultOut,
    WanTargetCreate,
    WanTargetOut,
    WanTargetUpdate,
)
from app.services import audit, wan_service
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/wan", tags=["wan"])


@router.get("/targets", response_model=list[WanTargetOut])
def list_targets(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[WanTarget]:
    return list(db.execute(select(WanTarget).order_by(WanTarget.id)).scalars().all())


@router.post("/targets", response_model=WanTargetOut, status_code=status.HTTP_201_CREATED)
def create_target(
    payload: WanTargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WanTarget:
    target = WanTarget(**payload.model_dump())
    db.add(target)
    audit.record(db, "wan_target_create", user_id=user.id, username=user.username,
                 detail=payload.name, ip=client_ip(request))
    db.commit()
    db.refresh(target)
    scheduler_service.sync_wan_jobs()
    return target


@router.patch("/targets/{target_id}", response_model=WanTargetOut)
def update_target(
    target_id: int,
    payload: WanTargetUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WanTarget:
    target = db.get(WanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(target, k, v)
    audit.record(db, "wan_target_update", user_id=user.id, username=user.username, detail=str(target_id))
    db.commit()
    db.refresh(target)
    scheduler_service.sync_wan_jobs()
    return target


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    target = db.get(WanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    db.delete(target)
    audit.record(db, "wan_target_delete", user_id=user.id, username=user.username, detail=str(target_id))
    db.commit()
    scheduler_service.sync_wan_jobs()


@router.get("/targets/{target_id}/results", response_model=list[WanResultOut])
def target_results(
    target_id: int,
    range: str = "24h",
    start: str | None = None,
    end: str | None = None,
    limit: int = 3000,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WanResult]:
    since, until = resolve_range(range, start, end, default="24h")
    conds = [WanResult.target_id == target_id]
    if since is not None:
        conds.append(WanResult.ts >= since)
    if until is not None:
        conds.append(WanResult.ts <= until)
    return thinned_rows(db, WanResult, conds, WanResult.ts, min(max(limit, 1), 10000))


@router.post("/targets/{target_id}/run", response_model=WanResultOut, status_code=status.HTTP_201_CREATED)
async def run_target_now(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WanResult:
    target = db.get(WanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")

    def _job() -> int:
        with session_scope() as s:
            t = s.get(WanTarget, target_id)
            if t is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
            res = wan_service.run_target(s, t)
            return res.id

    result_id = await asyncio.to_thread(_job)
    result = db.get(WanResult, result_id)
    if result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Ergebnis nicht gefunden")
    return result

