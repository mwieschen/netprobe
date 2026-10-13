"""Laufzeit-Einstellungen (nur admin). Persistiert im KV-Store und wirkt auf
den Scheduler."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.schemas import SettingsOut, SettingsUpdate
from app.services import audit, kvstore
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current(db: Session) -> SettingsOut:
    return SettingsOut(
        speedtest_enabled=kvstore.get_value(db, "speedtest_enabled", settings.speedtest_enabled),
        speedtest_interval_min=kvstore.get_value(
            db, "speedtest_interval_min", settings.speedtest_interval_min
        ),
        speedtest_server_id=kvstore.get_value(db, "speedtest_server_id", settings.speedtest_server_id),
        retrans_enabled=kvstore.get_value(db, "retrans_enabled", settings.retrans_enabled),
        retrans_scheduled_interval_min=kvstore.get_value(
            db, "retrans_scheduled_interval_min", settings.retrans_scheduled_interval_min
        ),
        capture_max_duration_s=settings.capture_max_duration_s,
        capture_max_size_mb=settings.capture_max_size_mb,
        ping_raw_retention_days=settings.ping_raw_retention_days,
        timezone=settings.timezone,
    )


@router.get("", response_model=SettingsOut)
def get_current_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> SettingsOut:
    return _current(db)


@router.patch("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> SettingsOut:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        kvstore.set_value(db, key, value)
        # Laufzeit-Settings-Objekt spiegeln, damit Scheduler die neuen Werte nutzt
        if hasattr(settings, key):
            setattr(settings, key, value)
    audit.record(db, "settings_update", user_id=user.id, username=user.username,
                 detail=str(data), ip=client_ip(request))
    db.commit()
    scheduler_service.reschedule_speedtest()
    scheduler_service.reschedule_retransmission()
    return _current(db)
