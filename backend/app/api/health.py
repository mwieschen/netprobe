"""Health- und Dashboard-Endpunkte."""

from __future__ import annotations

import shutil
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models.health_check import HealthCheckResult, HealthProbe
from app.models.ping import PingJob, PingSample
from app.models.retransmission import RetransmissionTest
from app.models.speedtest import SpeedtestResult
from app.models.traceroute import Traceroute, TracerouteTarget
from app.models.user import User
from app.models.voip import VoipServer, VoipTest
from app.models.wan import WanResult, WanTarget
from app.schemas.schemas import DashboardHealth, SpeedtestOut

router = APIRouter(prefix="/api", tags=["health"])


def _tool_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def _dir_size_mb(path) -> float:
    total = 0
    try:
        for p in path.glob("**/*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return round(total / (1024 * 1024), 2)


@router.get("/health")
def health() -> dict:
    """Öffentlicher Healthcheck für Monitoring/Reverse-Proxy."""
    return {"status": "ok"}


@router.get("/dashboard/health", response_model=DashboardHealth)
def dashboard_health(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> DashboardHealth:
    usage = shutil.disk_usage(str(settings.data_dir if settings.data_dir.exists() else "."))
    db_path = settings.data_dir / "db.sqlite"
    db_size = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0.0

    tools = {
        "speedtest": _tool_available(settings.speedtest_bin),
        "fping": _tool_available(settings.fping_bin),
        "ping": _tool_available(settings.ping_bin),
        "dumpcap": _tool_available(settings.dumpcap_bin),
        "tshark": _tool_available(settings.tshark_bin),
        "iperf3": _tool_available(settings.iperf3_bin),
        "mtr": _tool_available(settings.mtr_bin),
    }
    active = db.query(PingJob).filter(PingJob.status == "running").count()
    status = "ok" if all([tools["ping"]]) else "degraded"

    return DashboardHealth(
        status=status,
        tools=tools,
        disk_free_mb=round(usage.free / (1024 * 1024), 2),
        disk_used_pct=round((usage.used / usage.total) * 100, 1),
        db_size_mb=db_size,
        captures_size_mb=_dir_size_mb(settings.capture_dir),
        active_ping_jobs=active,
    )


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    latest_speed = db.execute(
        select(SpeedtestResult).order_by(SpeedtestResult.ts.desc()).limit(1)
    ).scalar_one_or_none()

    # Aktuellste WAN-Werte je Ziel
    targets = db.execute(select(WanTarget)).scalars().all()
    wan_current = []
    for t in targets:
        last = db.execute(
            select(WanResult).where(WanResult.target_id == t.id).order_by(WanResult.ts.desc()).limit(1)
        ).scalar_one_or_none()
        wan_current.append(
            {
                "target_id": t.id,
                "name": t.name,
                "host": t.host,
                "enabled": t.enabled,
                "ok": last.ok if last else None,
                "rtt_avg": last.rtt_avg if last else None,
                "jitter_ms": last.jitter_ms if last else None,
                "loss_pct": last.loss_pct if last else None,
                "ts": last.ts.isoformat() if last else None,
            }
        )

    # Aktuellster VoIP-Test je 3CX-Server
    voip_servers = db.execute(select(VoipServer)).scalars().all()
    voip_current = []
    for vs in voip_servers:
        vlast = db.execute(
            select(VoipTest).where(VoipTest.server_id == vs.id).order_by(VoipTest.ts.desc()).limit(1)
        ).scalar_one_or_none()
        voip_current.append(
            {
                "server_id": vs.id,
                "name": vs.name,
                "host": vs.host,
                "enabled": vs.enabled,
                "ok": vlast.ok if vlast else None,
                "sip_response_ms": vlast.sip_response_ms if vlast else None,
                "loss_pct": vlast.loss_pct if vlast else None,
                "ts": vlast.ts.isoformat() if vlast else None,
            }
        )

    # System-Health-Checks: aktueller Stand je Probe (ok/fail/pending)
    health_probes = db.execute(select(HealthProbe)).scalars().all()
    hc_ok = hc_fail = hc_pending = 0
    hc_failing: list[dict] = []
    for hp in health_probes:
        hc_last = db.execute(
            select(HealthCheckResult)
            .where(HealthCheckResult.probe_id == hp.id)
            .order_by(HealthCheckResult.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if hc_last is None:
            hc_pending += 1
        elif hc_last.ok:
            hc_ok += 1
        else:
            hc_fail += 1
            hc_failing.append({"name": hp.name, "check_type": hp.check_type,
                               "status": hc_last.status, "error": hc_last.error})

    since = utcnow() - timedelta(hours=24)
    disruptions = db.execute(
        select(func.count()).select_from(WanResult).where(WanResult.ok == False, WanResult.ts >= since)  # noqa: E712
    ).scalar()

    active_pings = db.query(PingJob).filter(PingJob.status == "running").count()

    latest_retrans = db.execute(
        select(RetransmissionTest).order_by(RetransmissionTest.ts.desc()).limit(1)
    ).scalar_one_or_none()
    retransmission = None
    if latest_retrans is not None:
        retransmission = {
            "id": latest_retrans.id,
            "ts": latest_retrans.ts.isoformat(),
            "interface": latest_retrans.interface,
            "status": latest_retrans.status,
            "rate_percent": latest_retrans.retransmission_rate_percent,
            "classification": latest_retrans.classification,
        }

    return {
        "latest_speedtest": SpeedtestOut.model_validate(latest_speed).model_dump()
        if latest_speed
        else None,
        "wan_current": wan_current,
        "voip_current": voip_current,
        "health_checks": {
            "ok": hc_ok, "fail": hc_fail, "pending": hc_pending, "failing": hc_failing,
        },
        "active_ping_jobs": active_pings,
        "disruptions_24h": disruptions or 0,
        "retransmission": retransmission,
    }


def _latest(db: Session, model, ts_col, where):
    return db.execute(
        select(model).where(where).order_by(ts_col.desc()).limit(1)
    ).scalar_one_or_none()


@router.get("/dashboard/topology")
def dashboard_topology(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    """Alle konfigurierten Ziele mit letztem Status – gruppiert nach Kategorie."""
    groups: dict[str, list[dict]] = {
        "wan": [], "ping": [], "voip": [], "traceroute": [], "health": [],
    }

    for t in db.execute(select(WanTarget).order_by(WanTarget.id)).scalars():
        last = _latest(db, WanResult, WanResult.ts, WanResult.target_id == t.id)
        groups["wan"].append({
            "id": t.id, "name": t.name, "target": t.host, "enabled": t.enabled,
            "ok": (last.ok if last else None) if t.enabled else None,
            "detail": (f"{last.rtt_avg:.0f} ms" if last and last.rtt_avg is not None else None),
            "ts": last.ts.isoformat() if last else None,
        })

    for j in db.execute(select(PingJob).order_by(PingJob.id)).scalars():
        last = _latest(db, PingSample, PingSample.ts, PingSample.job_id == j.id)
        groups["ping"].append({
            "id": j.id, "name": j.name, "target": j.host, "enabled": j.status == "running",
            "ok": (last.ok if last else None) if j.status == "running" else None,
            "detail": j.status,
            "ts": last.ts.isoformat() if last else None,
        })

    for s in db.execute(select(VoipServer).order_by(VoipServer.id)).scalars():
        last = _latest(db, VoipTest, VoipTest.ts, VoipTest.server_id == s.id)
        groups["voip"].append({
            "id": s.id, "name": s.name, "target": s.host, "enabled": s.enabled,
            "ok": (last.ok if last else None) if s.enabled else None,
            "detail": (last.status if last and last.error else None) if last else None,
            "ts": last.ts.isoformat() if last else None,
        })

    for trt in db.execute(select(TracerouteTarget).order_by(TracerouteTarget.id)).scalars():
        last = _latest(db, Traceroute, Traceroute.ts, Traceroute.target_id == trt.id)
        groups["traceroute"].append({
            "id": trt.id, "name": trt.name, "target": trt.host, "enabled": trt.enabled,
            "ok": (last.dest_reached if last and last.completed else None) if trt.enabled else None,
            "detail": (f"{last.total_hops} Hops" if last else None),
            "ts": last.ts.isoformat() if last else None,
        })

    for p in db.execute(select(HealthProbe).order_by(HealthProbe.check_type, HealthProbe.id)).scalars():
        last = _latest(db, HealthCheckResult, HealthCheckResult.ts,
                       HealthCheckResult.probe_id == p.id)
        groups["health"].append({
            "id": p.id, "name": p.name, "target": f"{p.check_type}: {p.target}",
            "enabled": p.enabled,
            "ok": (last.ok if last else None) if p.enabled else None,
            "detail": last.status if last else None,
            "ts": last.ts.isoformat() if last else None,
        })

    return {"groups": groups}


@router.get("/dashboard/timeline")
def dashboard_timeline(
    hours: int = 24,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Chronologische Liste der Störungen (fehlgeschlagene Messungen) im Fenster."""
    since = utcnow() - timedelta(hours=max(1, min(hours, 720)))
    limit = max(1, min(limit, 1000))
    events: list[dict] = []

    def names(id_col, name_col) -> dict:
        return {row[0]: row[1] for row in db.execute(select(id_col, name_col)).all()}

    wan_names = names(WanTarget.id, WanTarget.name)
    for wr in db.execute(
        select(WanResult).where(WanResult.ok == False, WanResult.ts >= since)  # noqa: E712
        .order_by(WanResult.ts.desc()).limit(limit)
    ).scalars():
        events.append({"ts": wr.ts.isoformat(), "category": "wan",
                       "name": wan_names.get(wr.target_id, f"Ziel {wr.target_id}"),
                       "detail": wr.error or "Timeout/Ausfall"})

    voip_names = names(VoipServer.id, VoipServer.name)
    for vt in db.execute(
        select(VoipTest).where(VoipTest.ok == False, VoipTest.ts >= since)  # noqa: E712
        .order_by(VoipTest.ts.desc()).limit(limit)
    ).scalars():
        events.append({"ts": vt.ts.isoformat(), "category": "voip",
                       "name": voip_names.get(vt.server_id, f"Server {vt.server_id}"),
                       "detail": vt.error or "SIP/TLS nicht erreichbar"})

    probe_names = names(HealthProbe.id, HealthProbe.name)
    for hcr in db.execute(
        select(HealthCheckResult).where(HealthCheckResult.ok == False, HealthCheckResult.ts >= since)  # noqa: E712
        .order_by(HealthCheckResult.ts.desc()).limit(limit)
    ).scalars():
        events.append({"ts": hcr.ts.isoformat(), "category": "health",
                       "name": probe_names.get(hcr.probe_id, f"Probe {hcr.probe_id}"),
                       "detail": hcr.error or hcr.status or "fehlgeschlagen"})

    tr_names = names(TracerouteTarget.id, TracerouteTarget.name)
    for tr in db.execute(
        select(Traceroute).where(
            Traceroute.completed == True, Traceroute.dest_reached == False,  # noqa: E712
            Traceroute.ts >= since, Traceroute.target_id.is_not(None),
        ).order_by(Traceroute.ts.desc()).limit(limit)
    ).scalars():
        events.append({"ts": tr.ts.isoformat(), "category": "traceroute",
                       "name": tr_names.get(tr.target_id, tr.target),
                       "detail": tr.error or "Ziel nicht erreicht"})

    events.sort(key=lambda e: e["ts"], reverse=True)
    return {"since": since.isoformat(), "events": events[:limit]}
