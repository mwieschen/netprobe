"""Globale Scheduler-Übersicht und Automatisierungs-Schalter.

Zeigt alle automatisierten Tests (geplante Speedtests/Retransmission-Monitoring
sowie jedes einzelne WAN-/VoIP-/Traceroute-/Health-Ziel) in einer Liste mit
Status, Intervall, nächstem/letztem Lauf. Reine Lese-Übersicht – das Bearbeiten
von Intervallen bleibt auf der jeweiligen Feature-Seite (bzw. für Speedtest/
Retransmission auf der Einstellungen-Seite), um keine zweite Konfigurationsquelle
zu schaffen. ``enable``/``disable`` pausiert/resumed alle Test-Jobs im laufenden
Prozess sofort (siehe ``app.workers.scheduler.SchedulerService``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.health_check import HealthCheckResult, HealthProbe
from app.models.retransmission import RetransmissionTest
from app.models.speedtest import SpeedtestResult
from app.models.traceroute import Traceroute, TracerouteTarget
from app.models.user import User
from app.models.voip import VoipServer, VoipTest
from app.models.wan import WanResult, WanTarget
from app.schemas.schemas import SchedulerStatusOut
from app.services import audit, capture_service, kvstore
from app.workers.scheduler import (
    HEALTH_JOB_PREFIX,
    RETRANS_JOB_ID,
    TRACEROUTE_JOB_PREFIX,
    VOIP_JOB_PREFIX,
    WAN_JOB_PREFIX,
    scheduler_service,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _job_next_run_and_paused(job_id: str) -> tuple[object | None, bool]:
    job = scheduler_service.scheduler.get_job(job_id)
    if job is None:
        return None, True
    return job.next_run_time, job.next_run_time is None


def _latest(db: Session, model, ts_col, where):
    return db.execute(select(model).where(where).order_by(ts_col.desc()).limit(1)).scalar_one_or_none()


def _build_jobs(db: Session) -> list[dict]:
    jobs: list[dict] = []

    # --- Speedtest (global, ein Job) ---
    sp_enabled = kvstore.get_value(db, "speedtest_enabled", settings.speedtest_enabled)
    sp_interval_min = kvstore.get_value(db, "speedtest_interval_min", settings.speedtest_interval_min)
    next_run, paused = _job_next_run_and_paused("speedtest")
    last_sp = db.execute(
        select(SpeedtestResult).order_by(SpeedtestResult.ts.desc()).limit(1)
    ).scalar_one_or_none()
    jobs.append({
        "id": "speedtest", "type": "speedtest", "label": "Speedtest", "target": None,
        "enabled": bool(sp_enabled and sp_interval_min > 0),
        "interval_seconds": int(sp_interval_min) * 60,
        "paused": paused if (sp_enabled and sp_interval_min > 0) else True,
        "next_run": next_run, "last_run": last_sp.ts if last_sp else None,
        "last_ok": last_sp.ok if last_sp else None, "page": "/speedtest",
    })

    # --- TCP-Retransmission-Monitoring (global, ein Job) ---
    rt_enabled = kvstore.get_value(db, "retrans_enabled", settings.retrans_enabled)
    rt_interval_min = kvstore.get_value(
        db, "retrans_scheduled_interval_min", settings.retrans_scheduled_interval_min
    )
    next_run, paused = _job_next_run_and_paused(RETRANS_JOB_ID)
    last_rt = db.execute(
        select(RetransmissionTest).order_by(RetransmissionTest.ts.desc()).limit(1)
    ).scalar_one_or_none()
    jobs.append({
        "id": RETRANS_JOB_ID, "type": "retransmission", "label": "TCP-Retransmission-Monitoring",
        "target": capture_service.resolve_default_interface(),
        "enabled": bool(rt_enabled and rt_interval_min > 0),
        "interval_seconds": int(rt_interval_min) * 60,
        "paused": paused if (rt_enabled and rt_interval_min > 0) else True,
        "next_run": next_run, "last_run": last_rt.ts if last_rt else None,
        "last_ok": (last_rt.status == "done") if last_rt else None, "page": "/retransmissions",
    })

    # --- WAN-Ziele (je Ziel ein Job) ---
    for t in db.execute(select(WanTarget).order_by(WanTarget.id)).scalars():
        next_run, paused = _job_next_run_and_paused(f"{WAN_JOB_PREFIX}{t.id}")
        last = _latest(db, WanResult, WanResult.ts, WanResult.target_id == t.id)
        jobs.append({
            "id": f"{WAN_JOB_PREFIX}{t.id}", "type": "wan", "label": t.name, "target": t.host,
            "enabled": t.enabled, "interval_seconds": t.interval_s,
            "paused": paused if t.enabled else True,
            "next_run": next_run, "last_run": last.ts if last else None,
            "last_ok": last.ok if last else None, "page": "/wan",
        })

    # --- VoIP/3CX-Server (je Server ein Job) ---
    for s in db.execute(select(VoipServer).order_by(VoipServer.id)).scalars():
        next_run, paused = _job_next_run_and_paused(f"{VOIP_JOB_PREFIX}{s.id}")
        last = _latest(db, VoipTest, VoipTest.ts, VoipTest.server_id == s.id)
        jobs.append({
            "id": f"{VOIP_JOB_PREFIX}{s.id}", "type": "voip", "label": s.name, "target": s.host,
            "enabled": s.enabled, "interval_seconds": s.interval_s,
            "paused": paused if s.enabled else True,
            "next_run": next_run, "last_run": last.ts if last else None,
            "last_ok": last.ok if last else None, "page": "/voip-monitor",
        })

    # --- Traceroute-Ziele (je Ziel ein Job) ---
    for tr in db.execute(select(TracerouteTarget).order_by(TracerouteTarget.id)).scalars():
        next_run, paused = _job_next_run_and_paused(f"{TRACEROUTE_JOB_PREFIX}{tr.id}")
        last = _latest(db, Traceroute, Traceroute.ts, Traceroute.target_id == tr.id)
        last_ok = (last.completed and last.dest_reached) if last else None
        jobs.append({
            "id": f"{TRACEROUTE_JOB_PREFIX}{tr.id}", "type": "traceroute", "label": tr.name,
            "target": tr.host, "enabled": tr.enabled, "interval_seconds": tr.interval_s,
            "paused": paused if tr.enabled else True,
            "next_run": next_run, "last_run": last.ts if last else None,
            "last_ok": last_ok, "page": "/traceroute",
        })

    # --- Health-Probes (je Probe ein Job) ---
    for p in db.execute(select(HealthProbe).order_by(HealthProbe.check_type, HealthProbe.id)).scalars():
        next_run, paused = _job_next_run_and_paused(f"{HEALTH_JOB_PREFIX}{p.id}")
        last = _latest(db, HealthCheckResult, HealthCheckResult.ts, HealthCheckResult.probe_id == p.id)
        jobs.append({
            "id": f"{HEALTH_JOB_PREFIX}{p.id}", "type": "health", "label": p.name,
            "target": f"{p.check_type}: {p.target}", "enabled": p.enabled, "interval_seconds": p.interval_s,
            "paused": paused if p.enabled else True,
            "next_run": next_run, "last_run": last.ts if last else None,
            "last_ok": last.ok if last else None, "page": "/health-checks",
        })

    return jobs


@router.get("/status", response_model=SchedulerStatusOut)
def get_status(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> SchedulerStatusOut:
    return SchedulerStatusOut(enabled=scheduler_service.enabled, jobs=_build_jobs(db))


@router.post("/enable", response_model=SchedulerStatusOut)
def enable_scheduler(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> SchedulerStatusOut:
    kvstore.set_value(db, "scheduler_enabled", True)
    audit.record(db, "scheduler_enable", user_id=user.id, username=user.username, ip=client_ip(request))
    db.commit()
    scheduler_service.set_enabled(True)
    return SchedulerStatusOut(enabled=scheduler_service.enabled, jobs=_build_jobs(db))


@router.post("/disable", response_model=SchedulerStatusOut)
def disable_scheduler(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> SchedulerStatusOut:
    kvstore.set_value(db, "scheduler_enabled", False)
    audit.record(db, "scheduler_disable", user_id=user.id, username=user.username, ip=client_ip(request))
    db.commit()
    scheduler_service.set_enabled(False)
    return SchedulerStatusOut(enabled=scheduler_service.enabled, jobs=_build_jobs(db))
