"""TCP-Retransmission-Monitoring: Tests starten, Historie/Details abrufen,
Korrelation mit anderen Tests, Report-Export (über das bestehende Report-System)."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.config import settings
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.ping import PingSample
from app.models.retransmission import RetransmissionTargetConfig, RetransmissionTest
from app.models.speedtest import SpeedtestResult
from app.models.user import User
from app.models.wan import WanResult
from app.schemas.schemas import (
    RetransReportRequest,
    RetransStartRequest,
    RetransTargetConfigCreate,
    RetransTargetConfigOut,
    RetransTestDetail,
    RetransTestOut,
)
from app.services import audit, capture_service, report_service, retransmission_service
from app.services.capture_service import CaptureValidationError
from app.services.proc import ToolNotFoundError, validate_network_target
from app.services.report_service import ReportError

router = APIRouter(prefix="/api/retransmission", tags=["retransmission"])


def _detail(test: RetransmissionTest) -> RetransTestDetail:
    data = RetransTestDetail.model_validate(test).model_dump()
    data["configured_targets"] = json.loads(test.target_ips) if test.target_ips else []
    return RetransTestDetail(**data)


def _touch_target_configs(db: Session, targets: list) -> None:
    """Markiert gespeicherte Ziel-Presets, die bei einem aktiven Test verwendet
    wurden, als benutzt (last_used/usage_count) – best effort, kein Fehler bei
    fehlendem Treffer (frei eingegebene Ziele müssen keinem Preset entsprechen)."""
    for t in targets:
        cfg = db.execute(
            select(RetransmissionTargetConfig).where(
                RetransmissionTargetConfig.host == t.host, RetransmissionTargetConfig.port == t.port
            )
        ).scalar_one_or_none()
        if cfg is not None:
            cfg.last_used = utcnow()
            cfg.usage_count += 1
    db.commit()


@router.post("/tests", response_model=RetransTestDetail, status_code=status.HTTP_201_CREATED)
async def start_test(
    payload: RetransStartRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> RetransTestDetail:
    """Startet einen Retransmission-Test (passiv oder aktiv). Läuft synchron im
    Threadpool (Dauer = ``duration_s``) und beeinträchtigt keine anderen laufenden
    Tests. Bei ``mode="active"`` werden die angegebenen Ziele per TCP-Connect-Probe
    angetestet, während parallel ein darauf gefilterter Mitschnitt läuft."""
    if payload.mode == "active" and not payload.targets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aktiver Test benötigt mindestens ein Ziel.")
    ip = client_ip(request)

    def _job() -> int:
        with session_scope() as s:
            test = retransmission_service.run_test(
                s,
                interface=payload.interface,
                duration_s=payload.duration_s,
                bpf=payload.bpf,
                source="manual",
                mode=payload.mode,
                targets=[t.model_dump() for t in payload.targets] or None,
                concurrent=payload.concurrent,
            )
            audit.record(
                s, "retransmission_test", user_id=user.id, username=user.username,
                detail=f"test={test.id} iface={test.interface} mode={payload.mode}", ip=ip,
            )
            if payload.mode == "active":
                _touch_target_configs(s, payload.targets)
            return test.id

    try:
        test_id = await asyncio.to_thread(_job)
    except (CaptureValidationError, ToolNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    test = db.get(RetransmissionTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Test nicht gefunden")
    return _detail(test)


@router.get("/tests", response_model=list[RetransTestOut])
def list_tests(
    days: int = 7,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[RetransTestOut]:
    limit = max(1, min(limit, 1000))
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = db.execute(
        select(RetransmissionTest)
        .where(RetransmissionTest.ts >= since)
        .order_by(RetransmissionTest.ts.desc())
        .limit(limit)
    ).scalars().all()
    return [RetransTestOut.model_validate(r) for r in rows]


@router.get("/tests/{test_id}", response_model=RetransTestDetail)
def get_test(
    test_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> RetransTestDetail:
    test = db.get(RetransmissionTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test nicht gefunden")
    return _detail(test)


@router.delete("/tests/{test_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test(
    test_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> None:
    test = db.get(RetransmissionTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test nicht gefunden")
    db.delete(test)
    audit.record(db, "retransmission_test_delete", user_id=user.id, username=user.username,
                 detail=str(test_id))
    db.commit()


@router.get("/tests/{test_id}/correlation")
def get_correlation(
    test_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    """Prüft, ob im Testfenster (± 5 Minuten) auch andere Messungen auffällig
    waren (Dauerping-Verluste, WAN-Ausfälle, Speedtest-Ergebnisse)."""
    test = db.get(RetransmissionTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test nicht gefunden")

    pad = timedelta(minutes=5)
    window_start = test.ts - pad
    window_end = test.ts + timedelta(seconds=test.duration_s) + pad

    ping_failures = db.execute(
        select(PingSample)
        .where(PingSample.ts.between(window_start, window_end), PingSample.ok.is_(False))
        .limit(200)
    ).scalars().all()

    wan_failures = db.execute(
        select(WanResult)
        .where(WanResult.ts.between(window_start, window_end), WanResult.ok.is_(False))
        .order_by(WanResult.ts)
        .limit(50)
    ).scalars().all()

    speedtests = db.execute(
        select(SpeedtestResult)
        .where(SpeedtestResult.ts.between(window_start, window_end))
        .order_by(SpeedtestResult.ts)
        .limit(10)
    ).scalars().all()

    return {
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "ping_failure_count": len(ping_failures),
        "wan_failures": [
            {"target_id": w.target_id, "ts": w.ts.isoformat(), "error": w.error} for w in wan_failures
        ],
        "speedtests": [
            {
                "ts": s.ts.isoformat(), "ok": s.ok,
                "download_mbps": s.download_mbps, "upload_mbps": s.upload_mbps,
            }
            for s in speedtests
        ],
        "correlated": bool(ping_failures) or bool(wan_failures) or any(not s.ok for s in speedtests),
    }


@router.post("/tests/{test_id}/report", status_code=status.HTTP_201_CREATED)
async def export_report(
    test_id: int,
    payload: RetransReportRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    """Erzeugt einen HTML-/PDF-Report für den Test (über das bestehende
    Report-System – gleiche Downloadroute wie Capture-/Job-Reports)."""
    if db.get(RetransmissionTest, test_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test nicht gefunden")
    ip = client_ip(request)

    def _job() -> int:
        with session_scope() as s:
            report = report_service.generate(
                s,
                capture_id=None,
                job_id=None,
                retrans_test_id=test_id,
                customer=payload.customer.strip(),
                location=payload.location.strip(),
                notes=payload.notes,
                sections=["summary", "retransmission"],
            )
            audit.record(
                s, "report_create", user_id=user.id, username=user.username,
                detail=f"report={report.id} retrans_test={test_id}", ip=ip,
            )
            return report.id

    try:
        report_id = await asyncio.to_thread(_job)
    except ReportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "report_id": report_id,
        "html_url": f"/api/reports/{report_id}/download?fmt=html",
        "pdf_url": f"/api/reports/{report_id}/download?fmt=pdf",
    }


@router.get("/targets", response_model=list[RetransTargetConfigOut])
def list_target_configs(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[RetransTargetConfigOut]:
    """Gespeicherte Ziel-Presets für aktive Tests (z. B. 'Cloudflare DNS')."""
    rows = db.execute(
        select(RetransmissionTargetConfig).order_by(RetransmissionTargetConfig.name)
    ).scalars().all()
    return [RetransTargetConfigOut.model_validate(r) for r in rows]


@router.post("/targets", response_model=RetransTargetConfigOut, status_code=status.HTTP_201_CREATED)
def create_target_config(
    payload: RetransTargetConfigCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> RetransTargetConfigOut:
    try:
        host = validate_network_target(payload.host)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    cfg = RetransmissionTargetConfig(
        name=payload.name, host=host, port=payload.port,
        protocol=payload.protocol, description=payload.description,
    )
    db.add(cfg)
    audit.record(db, "retransmission_target_create", user_id=user.id, username=user.username,
                 detail=f"{payload.name} ({host}:{payload.port})")
    db.commit()
    db.refresh(cfg)
    return RetransTargetConfigOut.model_validate(cfg)


@router.delete("/targets/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target_config(
    config_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> None:
    cfg = db.get(RetransmissionTargetConfig, config_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel-Preset nicht gefunden")
    db.delete(cfg)
    audit.record(db, "retransmission_target_delete", user_id=user.id, username=user.username,
                 detail=str(config_id))
    db.commit()


@router.get("/config")
def get_config(_: User = Depends(get_current_user)) -> dict:
    """Konfigurierte Default-/Schwellwerte (für die UI-Vorbelegung)."""
    return {
        "enabled": settings.retrans_enabled,
        "default_interface": capture_service.resolve_default_interface(),
        "default_duration_s": settings.retrans_default_duration_s,
        "max_duration_s": settings.retrans_max_duration_s,
        "warn_threshold_pct": settings.retrans_warn_threshold_pct,
        "alert_threshold_pct": settings.retrans_alert_threshold_pct,
        "scheduled_interval_min": settings.retrans_scheduled_interval_min,
        "max_active_targets": retransmission_service.MAX_ACTIVE_TARGETS,
    }
