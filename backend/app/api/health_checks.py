"""System-Health-Checks: Probes verwalten, ausführen, Historie und Übersicht."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.health_check import HealthCheckResult, HealthProbe
from app.models.user import User
from app.schemas.schemas import (
    HealthCheckResultOut,
    HealthProbeCreate,
    HealthProbeOut,
    HealthProbeUpdate,
)
from app.services import audit
from app.services import health as health_framework
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/health-checks", tags=["health-checks"])


def _json_or(default, raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _result_to_out(r: HealthCheckResult) -> HealthCheckResultOut:
    return HealthCheckResultOut(
        id=r.id, probe_id=r.probe_id, ts=r.ts, ok=r.ok, source=r.source,
        response_ms=r.response_ms, status=r.status,
        details=_json_or({}, r.details_json), error=r.error,
    )


def _probe_to_out(db: Session, probe: HealthProbe) -> HealthProbeOut:
    last = db.execute(
        select(HealthCheckResult)
        .where(HealthCheckResult.probe_id == probe.id)
        .order_by(HealthCheckResult.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    return HealthProbeOut(
        id=probe.id,
        name=probe.name,
        check_type=probe.check_type,
        target=probe.target,
        params=_json_or({}, probe.params_json),
        enabled=probe.enabled,
        interval_s=probe.interval_s,
        created_at=probe.created_at,
        last_result=_result_to_out(last) if last else None,
    )


@router.get("/types")
def list_types(_: User = Depends(get_current_user)) -> list[str]:
    return health_framework.available_types()


@router.get("", response_model=list[HealthProbeOut])
def list_probes(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[HealthProbeOut]:
    probes = db.execute(select(HealthProbe).order_by(HealthProbe.check_type, HealthProbe.id)).scalars().all()
    return [_probe_to_out(db, p) for p in probes]


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    probes = db.execute(select(HealthProbe)).scalars().all()
    items = [_probe_to_out(db, p) for p in probes]
    ok = sum(1 for i in items if i.last_result and i.last_result.ok)
    fail = sum(1 for i in items if i.last_result and not i.last_result.ok)
    pending = sum(1 for i in items if i.last_result is None)
    return {"total": len(items), "ok": ok, "fail": fail, "pending": pending,
            "probes": [i.model_dump() for i in items]}


@router.post("", response_model=HealthProbeOut, status_code=status.HTTP_201_CREATED)
def create_probe(
    payload: HealthProbeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> HealthProbeOut:
    probe = HealthProbe(
        name=payload.name, check_type=payload.check_type, target=payload.target,
        params_json=json.dumps(payload.params), enabled=payload.enabled,
        interval_s=payload.interval_s,
    )
    db.add(probe)
    audit.record(db, "health_probe_create", user_id=user.id, username=user.username,
                 detail=f"{payload.check_type}:{payload.name}", ip=client_ip(request))
    db.commit()
    db.refresh(probe)
    scheduler_service.sync_health_jobs()
    return _probe_to_out(db, probe)


@router.patch("/{probe_id}", response_model=HealthProbeOut)
def update_probe(
    probe_id: int,
    payload: HealthProbeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> HealthProbeOut:
    probe = db.get(HealthProbe, probe_id)
    if probe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
    data = payload.model_dump(exclude_unset=True)
    if "params" in data and data["params"] is not None:
        probe.params_json = json.dumps(data.pop("params"))
    else:
        data.pop("params", None)
    for k, v in data.items():
        setattr(probe, k, v)
    audit.record(db, "health_probe_update", user_id=user.id, username=user.username, detail=str(probe_id))
    db.commit()
    db.refresh(probe)
    scheduler_service.sync_health_jobs()
    return _probe_to_out(db, probe)


@router.delete("/{probe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_probe(
    probe_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    probe = db.get(HealthProbe, probe_id)
    if probe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
    db.delete(probe)
    audit.record(db, "health_probe_delete", user_id=user.id, username=user.username, detail=str(probe_id))
    db.commit()
    scheduler_service.sync_health_jobs()


@router.post("/{probe_id}/run", response_model=HealthCheckResultOut, status_code=status.HTTP_201_CREATED)
async def run_probe_now(
    probe_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> HealthCheckResultOut:
    probe = db.get(HealthProbe, probe_id)
    if probe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")

    audit.record(db, "health_probe_run", user_id=user.id, username=user.username, detail=str(probe_id))
    db.commit()

    def _job() -> int:
        with session_scope() as s:
            p = s.get(HealthProbe, probe_id)
            if p is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
            return health_framework.run_probe(s, p, source="manual").id

    result_id = await asyncio.to_thread(_job)
    result = db.get(HealthCheckResult, result_id)
    if result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Ergebnis nicht gefunden")
    return _result_to_out(result)


@router.get("/{probe_id}/history", response_model=list[HealthCheckResultOut])
def probe_history(
    probe_id: int,
    range: str = "24h",
    limit: int = 500,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[HealthCheckResultOut]:
    since = _range_to_since(range)
    q = (
        select(HealthCheckResult)
        .where(HealthCheckResult.probe_id == probe_id)
        .order_by(HealthCheckResult.ts.desc())
    )
    if since is not None:
        q = q.where(HealthCheckResult.ts >= since)
    rows = db.execute(q.limit(min(limit, 5000))).scalars().all()
    return [_result_to_out(r) for r in rows]


def _range_to_since(range: str):
    now = utcnow()
    mapping = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if range in mapping:
        return now - mapping[range]
    if range == "all":
        return None
    return now - timedelta(hours=24)
