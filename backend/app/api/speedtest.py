"""Speedtest-Endpunkte."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.speedtest import SpeedtestResult
from app.models.user import User
from app.schemas.schemas import SpeedtestOut, SpeedtestRunRequest
from app.services import audit, kvstore, speedtest_service

router = APIRouter(prefix="/api/speedtest", tags=["speedtest"])


@router.get("/results", response_model=list[SpeedtestOut])
def list_results(
    range: str = "24h",
    limit: int = 500,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SpeedtestResult]:
    since = _range_to_since(range)
    q = select(SpeedtestResult).order_by(SpeedtestResult.ts.desc())
    if since is not None:
        q = q.where(SpeedtestResult.ts >= since)
    return list(db.execute(q.limit(min(limit, 5000))).scalars().all())


@router.get("/latest", response_model=SpeedtestOut | None)
def latest(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> SpeedtestResult | None:
    return db.execute(
        select(SpeedtestResult).order_by(SpeedtestResult.ts.desc()).limit(1)
    ).scalar_one_or_none()


@router.get("/running")
def running(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return {"running": kvstore.is_locked(db, speedtest_service.SPEEDTEST_LOCK, ttl_s=600)}


@router.post("/run", response_model=SpeedtestOut, status_code=status.HTTP_201_CREATED)
async def run_now(
    payload: SpeedtestRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> SpeedtestResult:
    if not kvstore.acquire_lock(db, speedtest_service.SPEEDTEST_LOCK, ttl_s=600):
        raise HTTPException(status.HTTP_409_CONFLICT, "Es läuft bereits ein Speedtest.")

    audit.record(db, "speedtest_run", user_id=user.id, username=user.username, ip=client_ip(request))
    db.commit()

    def _job() -> int:
        with session_scope() as s:
            result = speedtest_service.run_speedtest(s, source="manual", server_id=payload.server_id)
            return result.id

    try:
        result_id = await asyncio.to_thread(_job)
    finally:
        with session_scope() as s:
            kvstore.release_lock(s, speedtest_service.SPEEDTEST_LOCK)

    result = db.get(SpeedtestResult, result_id)
    if result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Ergebnis nicht gefunden")
    return result


@router.get("/servers")
async def servers(_: User = Depends(require_admin)) -> list[dict]:
    return await asyncio.to_thread(speedtest_service.list_servers)


def _range_to_since(range: str):
    now = utcnow()
    mapping = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    if range in mapping:
        return now - mapping[range]
    if range == "all":
        return None
    return now - timedelta(hours=24)
