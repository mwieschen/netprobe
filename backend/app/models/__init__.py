"""ORM-Modelle. Der Import registriert alle Tabellen an der Metadata der Base."""

from app.models.audit import AuditLog
from app.models.capture import Capture
from app.models.capture_job import CaptureJob
from app.models.iperf import IperfResult
from app.models.mtu import MtuTest
from app.models.ping import PingAggregate, PingJob, PingSample
from app.models.report import Report
from app.models.settings import AppSetting
from app.models.speedtest import SpeedtestResult
from app.models.user import User
from app.models.wan import WanResult, WanTarget

__all__ = [
    "AppSetting",
    "AuditLog",
    "Capture",
    "CaptureJob",
    "IperfResult",
    "MtuTest",
    "PingAggregate",
    "PingJob",
    "PingSample",
    "Report",
    "SpeedtestResult",
    "User",
    "WanResult",
    "WanTarget",
]
