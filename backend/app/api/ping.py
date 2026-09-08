"""Dauerping: Jobs verwalten (Start/Stop/Pause/Löschen), Daten und SSE-Stream."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models.ping import PingAggregate, PingJob, PingSample
from app.models.user import User
from app.schemas.schemas import PingJobCreate, PingJobOut, PingSampleOut, PingStats
from app.services import audit
from app.services.events import ping_broker
from app.services.ping_manager import ping_manager

router = APIRouter(prefix="/api/ping", tags=["ping"])


@router.get("/jobs", response_model=list[PingJobOut])
def list_jobs(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[PingJob]:
    return list(db.execute(select(PingJob).order_by(PingJob.id)).scalars().all())


@router.post("/jobs", response_model=PingJobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: PingJobCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> PingJob:
    job = PingJob(name=payload.name, host=payload.host, interval_s=payload.interval_s, status="running")
    db.add(job)
    audit.record(db, "ping_job_create", user_id=user.id, username=user.username,
                 detail=payload.name, ip=client_ip(request))
    db.commit()
    db.refresh(job)
    ping_manager.start(job)
    return job


@router.post("/jobs/{job_id}/{action}", response_model=PingJobOut)
def control_job(
    job_id: int,
    action: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> PingJob:
    job = db.get(PingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    if action == "start":
        job.status = "running"
        db.commit()
        ping_manager.start(job)
    elif action in ("pause", "stop"):
        job.status = "paused" if action == "pause" else "stopped"
        db.commit()
        ping_manager.stop(job_id)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannte Aktion")
    audit.record(db, f"ping_job_{action}", user_id=user.id, username=user.username, detail=str(job_id))
    db.commit()
    db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    job = db.get(PingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    ping_manager.stop(job_id)
    db.delete(job)
    audit.record(db, "ping_job_delete", user_id=user.id, username=user.username, detail=str(job_id))
    db.commit()


@router.get("/jobs/{job_id}/samples", response_model=list[PingSampleOut])
def job_samples(
    job_id: int,
    range: str = "1h",
    limit: int = 5000,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PingSample]:
    since = _range_to_since(range)
    q = select(PingSample).where(PingSample.job_id == job_id).order_by(PingSample.ts.desc())
    if since is not None:
        q = q.where(PingSample.ts >= since)
    return list(db.execute(q.limit(min(limit, 20000))).scalars().all())


@router.get("/jobs/{job_id}/stats", response_model=PingStats)
def job_stats(
    job_id: int,
    range: str = "1h",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PingStats:
    since = _range_to_since(range)
    q = select(PingSample).where(PingSample.job_id == job_id)
    if since is not None:
        q = q.where(PingSample.ts >= since)
    samples = list(db.execute(q).scalars().all())
    rtts = [s.rtt_ms for s in samples if s.ok and s.rtt_ms is not None]
    loss = sum(1 for s in samples if not s.ok)
    count = len(samples)
    return PingStats(
        job_id=job_id,
        count=count,
        loss=loss,
        loss_pct=round(loss / count * 100, 2) if count else 0.0,
        rtt_min=round(min(rtts), 3) if rtts else None,
        rtt_avg=round(sum(rtts) / len(rtts), 3) if rtts else None,
        rtt_max=round(max(rtts), 3) if rtts else None,
    )


@router.get("/jobs/{job_id}/aggregates")
def job_aggregates(
    job_id: int,
    range: str = "7d",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    since = _range_to_since(range)
    q = select(PingAggregate).where(PingAggregate.job_id == job_id).order_by(PingAggregate.bucket_ts)
    if since is not None:
        q = q.where(PingAggregate.bucket_ts >= since)
    rows = db.execute(q).scalars().all()
    return [
        {
            "bucket_ts": r.bucket_ts.isoformat(),
            "count": r.count,
            "loss": r.loss,
            "rtt_min": r.rtt_min,
            "rtt_avg": r.rtt_avg,
            "rtt_max": r.rtt_max,
        }
        for r in rows
    ]


@router.get("/stream")
async def stream(_: User = Depends(get_current_user)) -> StreamingResponse:
    async def event_gen():
        async for chunk in ping_broker.subscribe():
            yield chunk

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _range_to_since(range: str):
    now = utcnow()
    mapping = {
        "5m": timedelta(minutes=5),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    if range in mapping:
        return now - mapping[range]
    if range == "all":
        return None
    return now - timedelta(hours=1)
