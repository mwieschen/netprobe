"""Dauerping: Jobs verwalten (Start/Stop/Pause/Löschen), Daten und SSE-Stream."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.api.timerange import apply_range, resolve_range
from app.db.session import get_db
from app.models.ping import PingAggregate, PingJob, PingSample
from app.models.user import User
from app.schemas.schemas import PingJobCreate, PingJobOut, PingSampleOut, PingSeriesPoint, PingStats
from app.services import audit, ping_history
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
    start: str | None = None,
    end: str | None = None,
    limit: int = 5000,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PingSample]:
    since, until = resolve_range(range, start, end, default="1h")
    q = select(PingSample).where(PingSample.job_id == job_id).order_by(PingSample.ts.desc())
    q = apply_range(q, PingSample.ts, since, until)
    return list(db.execute(q.limit(min(limit, 20000))).scalars().all())


@router.get("/jobs/{job_id}/series", response_model=list[PingSeriesPoint])
def job_series(
    job_id: int,
    range: str = "1h",
    start: str | None = None,
    end: str | None = None,
    points: int = 1500,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    """Diagramm-Zeitreihe über den gesamten Zeitraum (gebündelt, inkl. Aggregaten)."""
    since, until = resolve_range(range, start, end, default="1h")
    return ping_history.series(db, job_id, since, until, min(max(points, 50), 5000))


@router.get("/jobs/{job_id}/stats", response_model=PingStats)
def job_stats(
    job_id: int,
    range: str = "1h",
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PingStats:
    since, until = resolve_range(range, start, end, default="1h")
    return PingStats(**ping_history.stats(db, job_id, since, until))


@router.get("/jobs/{job_id}/aggregates")
def job_aggregates(
    job_id: int,
    range: str = "7d",
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    since, until = resolve_range(range, start, end, default="7d")
    q = select(PingAggregate).where(PingAggregate.job_id == job_id).order_by(PingAggregate.bucket_ts)
    q = apply_range(q, PingAggregate.bucket_ts, since, until)
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

