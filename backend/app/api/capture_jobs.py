"""Langzeit-Captures (Ringpuffer): Jobs anlegen/starten/stoppen/löschen,
rotierte Dateien auflisten und herunterladen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models.capture_job import CaptureJob
from app.models.user import User
from app.schemas.schemas import CaptureJobCreate, CaptureJobFile, CaptureJobOut
from app.services import audit
from app.services.capture_service import CaptureValidationError, validate_bpf, validate_iface
from app.services.long_capture import (
    clamp_job_limits,
    delete_job_dir,
    list_job_files,
    long_capture_manager,
    resolve_job_file,
)

router = APIRouter(prefix="/api/capture-jobs", tags=["capture-jobs"])


@router.get("", response_model=list[CaptureJobOut])
def list_jobs(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[CaptureJob]:
    return list(db.execute(select(CaptureJob).order_by(CaptureJob.id.desc())).scalars().all())


@router.post("", response_model=CaptureJobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: CaptureJobCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> CaptureJob:
    try:
        validate_iface(payload.iface)
        validate_bpf(payload.bpf)
    except CaptureValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    size_mb, files = clamp_job_limits(payload.max_file_size_mb, payload.max_files)
    job = CaptureJob(
        name=payload.name or f"{payload.iface} {utcnow():%Y-%m-%d %H:%M}",
        iface=payload.iface,
        bpf=payload.bpf or None,
        max_file_size_mb=size_mb,
        max_files=files,
        status="running",
    )
    db.add(job)
    audit.record(db, "capture_job_create", user_id=user.id, username=user.username,
                 detail=f"iface={payload.iface} {size_mb}MBx{files}", ip=client_ip(request))
    db.commit()
    db.refresh(job)

    await long_capture_manager.start(job.id)
    db.refresh(job)
    return job


@router.post("/{job_id}/start", response_model=CaptureJobOut)
async def start_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> CaptureJob:
    job = db.get(CaptureJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    job.status = "running"
    job.error = None
    job.stopped_at = None
    audit.record(db, "capture_job_start", user_id=user.id, username=user.username, detail=str(job_id))
    db.commit()
    await long_capture_manager.start(job_id)
    db.refresh(job)
    return job


@router.post("/{job_id}/stop", response_model=CaptureJobOut)
async def stop_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> CaptureJob:
    job = db.get(CaptureJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    job.status = "stopped"
    job.stopped_at = utcnow()
    audit.record(db, "capture_job_stop", user_id=user.id, username=user.username, detail=str(job_id))
    db.commit()
    await long_capture_manager.stop(job_id)
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    job = db.get(CaptureJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    # Erst Status setzen und committen, damit der Watchdog des Managers das
    # SIGTERM-bedingte Prozessende nicht als Fehler wertet.
    job.status = "stopped"
    db.commit()
    await long_capture_manager.stop(job_id)
    delete_job_dir(job_id)
    db.delete(job)
    audit.record(db, "capture_job_delete", user_id=user.id, username=user.username, detail=str(job_id))
    db.commit()


@router.get("/{job_id}/captures", response_model=list[CaptureJobFile])
def job_captures(
    job_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[dict]:
    job = db.get(CaptureJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    return list_job_files(job_id)


@router.get("/{job_id}/captures/{name}/download")
def download_job_capture(
    job_id: int, name: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    if db.get(CaptureJob, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    path = resolve_job_file(job_id, name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Datei nicht gefunden")
    return FileResponse(path, media_type="application/vnd.tcpdump.pcap", filename=path.name)
