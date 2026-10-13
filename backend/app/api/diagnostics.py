"""Network Diagnostics: mehrere bestehende Tests (Ping, Retransmissions, MTU,
Broadcast, Traceroute, Speedtest) gebündelt zu mehreren Zielen ausführen und als
Ampel-Übersicht (OK/WARN/KRITISCH) darstellen. Gespeicherte Ziel-/Test-Presets
für wiederkehrende Checks."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models.diagnostics import DiagnosticPreset, DiagnosticRun
from app.models.user import User
from app.schemas.schemas import (
    DiagnosticPresetCreate,
    DiagnosticPresetOut,
    DiagnosticRunOut,
    DiagnosticRunRequest,
)
from app.services import audit, diagnostics_service
from app.services.capture_service import CaptureValidationError
from app.services.diagnostics_service import DiagnosticsError
from app.services.proc import ToolNotFoundError

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


def _json_or(default, raw: str | None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _run_to_out(run: DiagnosticRun) -> DiagnosticRunOut:
    return DiagnosticRunOut(
        id=run.id, ts=run.ts, interface=run.interface, duration_s=run.duration_s,
        status=run.status, overall_status=run.overall_status,
        targets=_json_or([], run.targets_json), tests=_json_or({}, run.tests_json),
        results=_json_or(None, run.results_json), error=run.error,
    )


def _preset_to_out(preset: DiagnosticPreset) -> DiagnosticPresetOut:
    return DiagnosticPresetOut(
        id=preset.id, name=preset.name,
        targets=_json_or([], preset.targets_json), tests=_json_or({}, preset.tests_json),
        interface=preset.interface, duration_s=preset.duration_s, created_at=preset.created_at,
    )


@router.post("/run", response_model=DiagnosticRunOut, status_code=status.HTTP_201_CREATED)
async def run_diagnostics(
    payload: DiagnosticRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> DiagnosticRunOut:
    """Startet einen Diagnose-Lauf. Läuft synchron im Threadpool (Dauer ≈
    ``duration_s``, die Sub-Tests laufen parallel) und beeinträchtigt keine
    anderen laufenden Tests."""
    ip = client_ip(request)

    def _job() -> int:
        run = diagnostics_service.run_diagnostics(
            targets=[t.model_dump() for t in payload.targets],
            tests=payload.tests.model_dump(),
            interface=payload.interface,
            duration_s=payload.duration_s,
        )
        return run.id

    try:
        run_id = await asyncio.to_thread(_job)
    except (CaptureValidationError, ToolNotFoundError, DiagnosticsError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    run = db.get(DiagnosticRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Lauf nicht gefunden")
    audit.record(
        db, "diagnostics_run", user_id=user.id, username=user.username,
        detail=f"run={run.id} iface={run.interface} status={run.overall_status}", ip=ip,
    )
    db.commit()
    return _run_to_out(run)


@router.get("/runs", response_model=list[DiagnosticRunOut])
def list_runs(
    days: int = 7, limit: int = 100,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
) -> list[DiagnosticRunOut]:
    limit = max(1, min(limit, 1000))
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = db.execute(
        select(DiagnosticRun)
        .where(DiagnosticRun.ts >= since)
        .order_by(DiagnosticRun.ts.desc())
        .limit(limit)
    ).scalars().all()
    return [_run_to_out(r) for r in rows]


@router.get("/runs/{run_id}", response_model=DiagnosticRunOut)
def get_run(
    run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> DiagnosticRunOut:
    run = db.get(DiagnosticRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lauf nicht gefunden")
    return _run_to_out(run)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> None:
    run = db.get(DiagnosticRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lauf nicht gefunden")
    db.delete(run)
    audit.record(db, "diagnostics_run_delete", user_id=user.id, username=user.username, detail=str(run_id))
    db.commit()


@router.get("/presets", response_model=list[DiagnosticPresetOut])
def list_presets(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[DiagnosticPresetOut]:
    rows = db.execute(select(DiagnosticPreset).order_by(DiagnosticPreset.name)).scalars().all()
    return [_preset_to_out(p) for p in rows]


@router.post("/presets", response_model=DiagnosticPresetOut, status_code=status.HTTP_201_CREATED)
def create_preset(
    payload: DiagnosticPresetCreate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
) -> DiagnosticPresetOut:
    preset = DiagnosticPreset(
        name=payload.name,
        targets_json=json.dumps([t.model_dump() for t in payload.targets]),
        tests_json=json.dumps(payload.tests.model_dump()),
        interface=payload.interface, duration_s=payload.duration_s,
    )
    db.add(preset)
    audit.record(db, "diagnostics_preset_create", user_id=user.id, username=user.username,
                 detail=payload.name)
    db.commit()
    db.refresh(preset)
    return _preset_to_out(preset)


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(
    preset_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> None:
    preset = db.get(DiagnosticPreset, preset_id)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset nicht gefunden")
    db.delete(preset)
    audit.record(db, "diagnostics_preset_delete", user_id=user.id, username=user.username,
                 detail=str(preset_id))
    db.commit()
