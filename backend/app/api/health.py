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
from app.models.ping import PingJob
from app.models.speedtest import SpeedtestResult
from app.models.user import User
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

    since = utcnow() - timedelta(hours=24)
    disruptions = db.execute(
        select(func.count()).select_from(WanResult).where(WanResult.ok == False, WanResult.ts >= since)  # noqa: E712
    ).scalar()

    active_pings = db.query(PingJob).filter(PingJob.status == "running").count()

    return {
        "latest_speedtest": SpeedtestOut.model_validate(latest_speed).model_dump()
        if latest_speed
        else None,
        "wan_current": wan_current,
        "active_ping_jobs": active_pings,
        "disruptions_24h": disruptions or 0,
    }
