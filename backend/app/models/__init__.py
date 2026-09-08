"""ORM-Modelle. Der Import registriert alle Tabellen an der Metadata der Base."""

from app.models.audit import AuditLog
from app.models.capture import Capture
from app.models.ping import PingAggregate, PingJob, PingSample
from app.models.settings import AppSetting
from app.models.speedtest import SpeedtestResult
from app.models.user import User
from app.models.wan import WanResult, WanTarget

__all__ = [
    "AppSetting",
    "AuditLog",
    "Capture",
    "PingAggregate",
    "PingJob",
    "PingSample",
    "SpeedtestResult",
    "User",
    "WanResult",
    "WanTarget",
]
