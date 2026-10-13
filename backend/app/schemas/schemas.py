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


# --- Langzeit-Captures ---
class CaptureJobCreate(BaseModel):
    name: str = Field(default="", max_length=128)
    iface: str = Field(min_length=1, max_length=64)
    bpf: str | None = Field(default=None, max_length=512)
    max_file_size_mb: int = Field(default=100, ge=1, le=10_000)
    max_files: int = Field(default=50, ge=1, le=1000)


class CaptureJobOut(ORMModel):
    id: int
    name: str
    iface: str
    bpf: str | None
    max_file_size_mb: int
    max_files: int
    status: str
    created_at: datetime
    stopped_at: datetime | None
    error: str | None


class CaptureJobFile(BaseModel):
    name: str
    size_bytes: int
    mtime: str


# --- iperf3 ---
class Iperf3ClientRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5201, ge=1, le=65535)
    duration_s: int = Field(default=10, ge=1, le=60)
    parallel: int = Field(default=1, ge=1, le=32)
    udp: bool = False
    reverse: bool = False


class Iperf3ResultOut(ORMModel):
    id: int
    ts: datetime
    ok: bool
    host: str
    port: int
    protocol: str
    duration_s: int
    parallel: int
    reverse: bool
    sent_mbps: float | None
    recv_mbps: float | None
    jitter_ms: float | None
    lost_percent: float | None
    lost_packets: int | None = None
    packets: int | None = None
    out_of_order: int | None = None
    retransmits: int | None
    error: str | None


class Iperf3ServerStatus(BaseModel):
    running: bool
    port: int


class Iperf3ServerRequest(BaseModel):
    enabled: bool
    port: int | None = Field(default=None, ge=1, le=65535)


# --- MTU-Test ---
class MtuTestRequest(BaseModel):
    target: str = Field(min_length=1, max_length=255)
    min_mtu: int = Field(default=576, ge=68, le=9000)
    max_mtu: int = Field(default=1500, ge=68, le=9216)


class MtuProbe(BaseModel):
    mtu: int
    ok: bool


class MtuTestResult(BaseModel):
    id: int
    ts: datetime
    target: str
    ok: bool
    path_mtu: int | None
    min_mtu: int
    max_mtu: int
    probe_count: int
    error: str | None
    probes: list[MtuProbe] = []


class MtuTestOut(ORMModel):
    id: int
    ts: datetime
    target: str
    ok: bool
    path_mtu: int | None
    min_mtu: int
    max_mtu: int
    probe_count: int
    error: str | None


# --- Reports ---
REPORT_SECTIONS = ("summary", "ping", "speedtest", "iperf3", "mtu", "passive", "retransmission")


class ReportCreate(BaseModel):
    capture_id: int | None = None
    job_id: int | None = None
    retrans_test_id: int | None = None
    customer: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    sections: list[str] = Field(default_factory=lambda: list(REPORT_SECTIONS))


class KeyMetric(BaseModel):
    label: str
    value: str
    sub: str
    level: str  # ok | warn | info | na


class Interpretation(BaseModel):
    level: str  # ok | warn | info
    text: str


class ReportOut(ORMModel):
    id: int
    ts: datetime
    title: str
    capture_id: int | None
    job_id: int | None
    retrans_test_id: int | None
    customer: str
    location: str
    notes: str | None
    sections: list[str]
    has_pdf: bool
    error: str | None
    html_url: str = ""
    pdf_url: str | None = None
    key_metrics: list[KeyMetric] = []
    interpretations: list[Interpretation] = []


class ReportPreview(BaseModel):
    source: dict
    sections: list[str]
    key_metrics: list[KeyMetric]
    interpretations: list[Interpretation]


# --- VoIP / 3CX ---
class VoipServerBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    sip_port: int = Field(default=5060, ge=1, le=65535)
    sip_tls_port: int = Field(default=5061, ge=0, le=65535)  # 0 = kein TLS-Test
    webrtc_ports: str = Field(default="443,5001", max_length=64)
    sip_transport: str = Field(default="udp", pattern="^(udp|tcp)$")
    check_registration: bool = False
    enabled: bool = True
    interval_s: int = Field(default=300, ge=60, le=86400)


class VoipServerCreate(VoipServerBase):
    pass


class VoipServerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    sip_port: int | None = Field(default=None, ge=1, le=65535)
    sip_tls_port: int | None = Field(default=None, ge=0, le=65535)
    webrtc_ports: str | None = Field(default=None, max_length=64)
    sip_transport: str | None = Field(default=None, pattern="^(udp|tcp)$")
    check_registration: bool | None = None
    enabled: bool | None = None
    interval_s: int | None = Field(default=None, ge=60, le=86400)


class VoipPortResult(BaseModel):
    port: int
    ok: bool
    ms: float | None = None


class VoipTestOut(BaseModel):
    id: int
    server_id: int
    ts: datetime
    ok: bool
    source: str
    sip_udp_ok: bool | None
    sip_tcp_ok: bool | None
    sip_tls_ok: bool | None
    webrtc_results: list[VoipPortResult] = []
    sip_response_ms: float | None
    sip_response_code: int | None
    tls_handshake_ms: float | None
    tls_verified: bool | None
    tls_cert_expiry: datetime | None
    loss_pct: float | None
    rtt_avg_ms: float | None
    registration_ok: bool | None
    registration_detail: str | None
    # M6: angereicherte OPTIONS-/TLS-Details + PCAP-Analyse
    server_header: str | None = None
    supported_methods: str | None = None
    tls_version: str | None = None
    tls_cipher: str | None = None
    cert_subject: str | None = None
    cert_issuer: str | None = None
    capture_path: str | None = None
    capture_url: str | None = None
    sip_analysis: dict = {}
    codec_analysis: dict = {}
    rtp_analysis: dict = {}
    error: str | None


class VoipServerOut(VoipServerBase):
    id: int
    created_at: datetime
    last_test: VoipTestOut | None = None


class VoipTestRequest(BaseModel):
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    capture_interface: str | None = Field(default=None, max_length=64)
    capture_duration_s: int = Field(default=0, ge=0, le=900)
    capture_bpf: str | None = Field(default=None, max_length=512)


class VoipAnalyzeRequest(BaseModel):
    capture_id: int | None = None
    capture_path: str | None = Field(default=None, max_length=512)


class VoipAnalysisOut(BaseModel):
    sip: dict = {}
    codecs: dict = {}
    rtp: dict = {}
    error: str | None = None


# --- Traceroute / Pfad-Analyse ---
class TracerouteTargetBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    mode: str = Field(default="icmp", pattern="^(icmp|udp|tcp)$")
    tcp_port: int = Field(default=443, ge=1, le=65535)
    max_hops: int = Field(default=30, ge=1, le=60)
    queries: int = Field(default=3, ge=1, le=10)
    resolve_geo: bool = True
    enabled: bool = True
    interval_s: int = Field(default=900, ge=60, le=86400)


class TracerouteTargetCreate(TracerouteTargetBase):
    pass


class TracerouteTargetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    mode: str | None = Field(default=None, pattern="^(icmp|udp|tcp)$")
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    max_hops: int | None = Field(default=None, ge=1, le=60)
    queries: int | None = Field(default=None, ge=1, le=10)
    resolve_geo: bool | None = None
    enabled: bool | None = None
    interval_s: int | None = Field(default=None, ge=60, le=86400)


class TracerouteTargetOut(TracerouteTargetBase):
    id: int
    created_at: datetime


class TracerouteRunRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    mode: str = Field(default="icmp", pattern="^(icmp|udp|tcp)$")
    tcp_port: int = Field(default=443, ge=1, le=65535)
    queries: int = Field(default=3, ge=1, le=10)
    max_hops: int = Field(default=30, ge=1, le=60)
    resolve_geo: bool = True
    measure_mtu: bool = False


class TracerouteHopOut(ORMModel):
    hop_number: int
    ip: str | None
    hostname: str | None
    latency_min_ms: float | None
    latency_avg_ms: float | None
    latency_max_ms: float | None
    loss_pct: float | None
    sent: int | None
    mtu: int | None
    country: str | None
    city: str | None
    asn: str | None
    isp: str | None
    lat: float | None
    lon: float | None


class TracerouteOut(ORMModel):
    id: int
    ts: datetime
    target_id: int | None
    target: str
    source: str
    mode: str
    engine: str
    total_hops: int
    completed: bool
    dest_reached: bool
    avg_latency_ms: float | None
    total_loss_pct: float | None
    error: str | None
    hops: list[TracerouteHopOut] = []


class TracerouteListItem(ORMModel):
    id: int
    ts: datetime
    target_id: int | None
    target: str
    source: str
    mode: str
    engine: str
    total_hops: int
    completed: bool
    dest_reached: bool
    avg_latency_ms: float | None
    total_loss_pct: float | None
    error: str | None


# --- System-Health-Checks ---
class HealthProbeBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    check_type: str = Field(pattern="^(dns|http|smtp|imap|ntp)$")
    target: str = Field(min_length=1, max_length=255)
    params: dict = Field(default_factory=dict)
    enabled: bool = True
    interval_s: int = Field(default=300, ge=60, le=86400)


class HealthProbeCreate(HealthProbeBase):
    pass


class HealthProbeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    target: str | None = Field(default=None, max_length=255)
    params: dict | None = None
    enabled: bool | None = None
    interval_s: int | None = Field(default=None, ge=60, le=86400)


class HealthCheckResultOut(ORMModel):
    id: int
    probe_id: int
    ts: datetime
    ok: bool
    source: str
    response_ms: float | None
    status: str | None
    details: dict = {}
    error: str | None


class HealthProbeOut(HealthProbeBase):
    id: int
    created_at: datetime
    last_result: HealthCheckResultOut | None = None


# --- TCP-Retransmission-Monitoring ---
class RetransTargetIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern="^tcp$")
    name: str | None = Field(default=None, max_length=128)


class RetransStartRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=64)
    duration_s: int = Field(default=60, ge=5, le=3600)
    bpf: str | None = Field(default=None, max_length=512)
    mode: str = Field(default="passive", pattern="^(passive|active)$")
    targets: list[RetransTargetIn] = Field(default_factory=list, max_length=10)
    concurrent: bool = True


class RetransTargetOut(ORMModel):
    target_ip: str
    target_port: int | None
    is_private: bool
    retransmissions: int
    packets: int
    rate_percent: float
    direction: str
    first_seen: datetime | None
    last_seen: datetime | None
    connection_attempts: int | None
    connection_errors: int | None
    avg_connect_latency_ms: float | None


class RetransTimelinePointOut(ORMModel):
    t_offset_s: float
    retrans_per_s: float
    packets_per_s: float
    rate_percent: float


class RetransTestOut(ORMModel):
    id: int
    ts: datetime
    source: str
    interface: str
    bpf: str | None
    duration_s: int
    test_mode: str
    concurrent: bool
    status: str
    total_packets: int | None
    tcp_packets: int | None
    total_retransmissions: int | None
    retransmission_rate_percent: float | None
    classification: str | None
    recommendation: str | None
    error: str | None


class RetransTestDetail(RetransTestOut):
    targets: list[RetransTargetOut] = []
    timeline: list[RetransTimelinePointOut] = []
    configured_targets: list[dict] = []


class RetransReportRequest(BaseModel):
    customer: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    notes: str | None = Field(default=None, max_length=5000)


# --- Gespeicherte Ziel-Presets für aktive Retransmission-Tests ---
class RetransTargetConfigBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern="^tcp$")
    description: str | None = Field(default=None, max_length=500)


class RetransTargetConfigCreate(RetransTargetConfigBase):
    pass


class RetransTargetConfigOut(RetransTargetConfigBase, ORMModel):
    id: int
    created_at: datetime
    last_used: datetime | None
    usage_count: int


# --- Network Diagnostics ---
class DiagnosticTargetIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    host: str = Field(min_length=1, max_length=255)


class DiagnosticTestSelection(BaseModel):
    ping: bool = True
    retrans: bool = False
    mtu: bool = False
    broadcast: bool = False
    traceroute: bool = False
    speedtest: bool = False


class DiagnosticRunRequest(BaseModel):
    targets: list[DiagnosticTargetIn] = Field(default_factory=list, max_length=10)
    tests: DiagnosticTestSelection = Field(default_factory=DiagnosticTestSelection)
    interface: str = Field(min_length=1, max_length=64)
    duration_s: int = Field(default=60, ge=5, le=3600)


class DiagnosticRunOut(ORMModel):
    id: int
    ts: datetime
    interface: str
    duration_s: int
    status: str
    overall_status: str | None
    targets: list[DiagnosticTargetIn]
    tests: DiagnosticTestSelection
    results: dict | None
    error: str | None


class DiagnosticPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    targets: list[DiagnosticTargetIn] = Field(default_factory=list, max_length=10)
    tests: DiagnosticTestSelection = Field(default_factory=DiagnosticTestSelection)
    interface: str = Field(min_length=1, max_length=64)
    duration_s: int = Field(default=60, ge=5, le=3600)


class DiagnosticPresetOut(ORMModel):
    id: int
    name: str
    targets: list[DiagnosticTargetIn]
    tests: DiagnosticTestSelection
    interface: str
    duration_s: int
    created_at: datetime


# --- Settings ---
class SettingsOut(BaseModel):
    speedtest_enabled: bool
    speedtest_interval_min: int
    speedtest_server_id: int | None
    retrans_enabled: bool
    retrans_scheduled_interval_min: int
    capture_max_duration_s: int
    capture_max_size_mb: int
    ping_raw_retention_days: int
    timezone: str


class SettingsUpdate(BaseModel):
    speedtest_enabled: bool | None = None
    speedtest_interval_min: int | None = Field(default=None, ge=0, le=1440)
    speedtest_server_id: int | None = None
    retrans_enabled: bool | None = None
    retrans_scheduled_interval_min: int | None = Field(default=None, ge=0, le=1440)


# --- Scheduler (Übersicht & globale Automatisierung) ---
class SchedulerJobOut(BaseModel):
    id: str
    type: str  # speedtest | retransmission | wan | voip | traceroute | health
    label: str
    target: str | None
    enabled: bool
    interval_seconds: int
    paused: bool
    next_run: datetime | None
    last_run: datetime | None
    last_ok: bool | None
    page: str


class SchedulerStatusOut(BaseModel):
    enabled: bool
    jobs: list[SchedulerJobOut]


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
