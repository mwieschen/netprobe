"""Pydantic-Schemas für Request/Response."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth / User ---
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="viewer", pattern="^(admin|viewer)$")


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: str | None = Field(default=None, pattern="^(admin|viewer)$")
    is_active: bool | None = None


# --- Speedtest ---
class SpeedtestOut(ORMModel):
    id: int
    ts: datetime
    ok: bool
    download_mbps: float | None
    upload_mbps: float | None
    ping_ms: float | None
    jitter_ms: float | None
    packet_loss: float | None
    server_id: int | None
    server_name: str | None
    source: str
    error: str | None


class SpeedtestRunRequest(BaseModel):
    server_id: int | None = None


# --- WAN ---
class WanTargetBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    interval_s: int = Field(default=60, ge=10, le=86400)
    count: int = Field(default=10, ge=1, le=100)
    timeout_ms: int = Field(default=1000, ge=100, le=10000)
    enabled: bool = True


class WanTargetCreate(WanTargetBase):
    pass


class WanTargetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    interval_s: int | None = Field(default=None, ge=10, le=86400)
    count: int | None = Field(default=None, ge=1, le=100)
    timeout_ms: int | None = Field(default=None, ge=100, le=10000)
    enabled: bool | None = None


class WanTargetOut(ORMModel):
    id: int
    name: str
    host: str
    interval_s: int
    count: int
    timeout_ms: int
    enabled: bool
    created_at: datetime


class WanResultOut(ORMModel):
    id: int
    target_id: int
    ts: datetime
    ok: bool
    loss_pct: float | None
    rtt_min: float | None
    rtt_avg: float | None
    rtt_max: float | None
    rtt_stddev: float | None
    jitter_ms: float | None
    error: str | None


# --- Dauerping ---
class PingJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    interval_s: int = Field(default=1, ge=1, le=3600)


class PingJobOut(ORMModel):
    id: int
    name: str
    host: str
    interval_s: int
    status: str
    created_at: datetime


class PingSampleOut(ORMModel):
    ts: datetime
    rtt_ms: float | None
    ok: bool


class PingStats(BaseModel):
    job_id: int
    count: int
    loss: int
    loss_pct: float
    rtt_min: float | None
    rtt_avg: float | None
    rtt_max: float | None


# --- Capture ---
class CaptureCreate(BaseModel):
    iface: str = Field(min_length=1, max_length=64)
    duration_s: int = Field(default=60, ge=1, le=900)
    max_packets: int | None = Field(default=None, ge=1, le=10_000_000)
    max_filesize_mb: int | None = Field(default=100, ge=1, le=500)
    ringbuffer: int | None = Field(default=None, ge=1, le=100)
    bpf: str | None = Field(default=None, max_length=512)


class CaptureOut(ORMModel):
    id: int
    ts: datetime
    iface: str
    status: str
    duration_s: int
    max_packets: int | None
    max_filesize_mb: int | None
    ringbuffer: int | None
    bpf: str | None
    size_bytes: int | None
    packet_count: int | None
    error: str | None


class DisplayFilterRequest(BaseModel):
    filter: str = Field(min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


# --- Settings ---
class SettingsOut(BaseModel):
    speedtest_enabled: bool
    speedtest_interval_min: int
    speedtest_server_id: int | None
    capture_max_duration_s: int
    capture_max_size_mb: int
    ping_raw_retention_days: int
    timezone: str


class SettingsUpdate(BaseModel):
    speedtest_enabled: bool | None = None
    speedtest_interval_min: int | None = Field(default=None, ge=0, le=1440)
    speedtest_server_id: int | None = None


# --- Audit ---
class AuditOut(ORMModel):
    id: int
    ts: datetime
    username: str | None
    action: str
    detail: str | None
    ip: str | None


# --- Dashboard ---
class DashboardHealth(BaseModel):
    status: str
    tools: dict[str, bool]
    disk_free_mb: float
    disk_used_pct: float
    db_size_mb: float
    captures_size_mb: float
    active_ping_jobs: int
