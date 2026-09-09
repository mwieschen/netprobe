"""Traceroute / Pfad-Analyse: Ziele verwalten, Läufe ausführen, vergleichen."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.traceroute import Traceroute, TracerouteTarget
from app.models.user import User
from app.schemas.schemas import (
    TracerouteListItem,
    TracerouteOut,
    TracerouteRunRequest,
    TracerouteTargetCreate,
    TracerouteTargetOut,
    TracerouteTargetUpdate,
)
from app.services import audit, traceroute_service
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/traceroute", tags=["traceroute"])


# --- Ziele -----------------------------------------------------------------
@router.get("/targets", response_model=list[TracerouteTargetOut])
def list_targets(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[TracerouteTarget]:
    return list(db.execute(select(TracerouteTarget).order_by(TracerouteTarget.id)).scalars().all())


@router.post("/targets", response_model=TracerouteTargetOut, status_code=status.HTTP_201_CREATED)
def create_target(
    payload: TracerouteTargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TracerouteTarget:
    target = TracerouteTarget(**payload.model_dump())
    db.add(target)
    audit.record(db, "traceroute_target_create", user_id=user.id, username=user.username,
                 detail=payload.name, ip=client_ip(request))
    db.commit()
    db.refresh(target)
    scheduler_service.sync_traceroute_jobs()
    return target


@router.patch("/targets/{target_id}", response_model=TracerouteTargetOut)
def update_target(
    target_id: int,
    payload: TracerouteTargetUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TracerouteTarget:
    target = db.get(TracerouteTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(target, k, v)
    audit.record(db, "traceroute_target_update", user_id=user.id, username=user.username,
                 detail=str(target_id))
    db.commit()
    db.refresh(target)
    scheduler_service.sync_traceroute_jobs()
    return target


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    target = db.get(TracerouteTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    db.delete(target)
    audit.record(db, "traceroute_target_delete", user_id=user.id, username=user.username,
                 detail=str(target_id))
    db.commit()
    scheduler_service.sync_traceroute_jobs()


# --- Läufe ---------------------------------------------------------------
def _run_job(payload: dict, source: str, target_id: int | None) -> int:
    with session_scope() as s:
        tr = traceroute_service.run(s, source=source, target_id=target_id, **payload)
        return tr.id


@router.post("/run", response_model=TracerouteOut, status_code=status.HTTP_201_CREATED)
async def run_now(
    payload: TracerouteRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Traceroute:
    audit.record(db, "traceroute_run", user_id=user.id, username=user.username,
                 detail=payload.host, ip=client_ip(request))
    db.commit()
    tr_id = await asyncio.to_thread(_run_job, payload.model_dump(), "manual", None)
    return _get_with_hops(db, tr_id)


@router.post("/targets/{target_id}/run", response_model=TracerouteOut, status_code=status.HTTP_201_CREATED)
async def run_target_now(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Traceroute:
    target = db.get(TracerouteTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    payload = {
        "host": target.host, "mode": target.mode, "tcp_port": target.tcp_port,
        "queries": target.queries, "max_hops": target.max_hops,
        "resolve_geo": target.resolve_geo, "measure_mtu": False,
    }
    audit.record(db, "traceroute_run", user_id=user.id, username=user.username,
                 detail=f"target={target_id}")
    db.commit()
    tr_id = await asyncio.to_thread(_run_job, payload, "manual", target_id)
    return _get_with_hops(db, tr_id)


@router.get("/history", response_model=list[TracerouteListItem])
def history(
    range: str = "7d",
    limit: int = 100,
    target_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Traceroute]:
    since = _range_to_since(range)
    q = select(Traceroute).order_by(Traceroute.ts.desc())
    if since is not None:
        q = q.where(Traceroute.ts >= since)
    if target_id is not None:
        q = q.where(Traceroute.target_id == target_id)
    return list(db.execute(q.limit(min(limit, 1000))).scalars().all())


@router.get("/compare", response_model=dict)
def compare(
    a: int,
    b: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    tr_a = _get_with_hops(db, a, allow_none=True)
    tr_b = _get_with_hops(db, b, allow_none=True)
    if tr_a is None or tr_b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Traceroute-Lauf nicht gefunden")
    return traceroute_service.compare(tr_a, tr_b)


@router.get("/{traceroute_id}", response_model=TracerouteOut)
def get_one(
    traceroute_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Traceroute:
    return _get_with_hops(db, traceroute_id)


def _get_with_hops(db: Session, traceroute_id: int, allow_none: bool = False):
    tr = db.execute(
        select(Traceroute)
        .where(Traceroute.id == traceroute_id)
        .options(selectinload(Traceroute.hops))
    ).scalar_one_or_none()
    if tr is None and not allow_none:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Traceroute-Lauf nicht gefunden")
    return tr


def _range_to_since(range: str):
    now = utcnow()
    mapping = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if range in mapping:
        return now - mapping[range]
    if range == "all":
        return None
    return now - timedelta(days=7)
