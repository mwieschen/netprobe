// Zentraler API-Client. Nutzt Session-Cookies (credentials: include).

export interface ApiError {
  status: number;
  detail: string;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const err: ApiError = { status: resp.status, detail: data?.detail ?? resp.statusText };
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, body?: unknown) => request<T>("POST", p, body),
  patch: <T>(p: string, body?: unknown) => request<T>("PATCH", p, body),
  del: <T>(p: string) => request<T>("DELETE", p),
};

// --- Typen ---
export interface User {
  id: number;
  username: string;
  role: "admin" | "viewer";
  is_active: boolean;
  created_at: string;
}

export interface Speedtest {
  id: number;
  ts: string;
  ok: boolean;
  download_mbps: number | null;
  upload_mbps: number | null;
  ping_ms: number | null;
  jitter_ms: number | null;
  packet_loss: number | null;
  server_id: number | null;
  server_name: string | null;
  source: string;
  error: string | null;
}

export interface WanTarget {
  id: number;
  name: string;
  host: string;
  interval_s: number;
  count: number;
  timeout_ms: number;
  enabled: boolean;
  created_at: string;
}

export interface WanResult {
  id: number;
  target_id: number;
  ts: string;
  ok: boolean;
  loss_pct: number | null;
  rtt_min: number | null;
  rtt_avg: number | null;
  rtt_max: number | null;
  rtt_stddev: number | null;
  jitter_ms: number | null;
  error: string | null;
}

export interface PingJob {
  id: number;
  name: string;
  host: string;
  interval_s: number;
  status: "running" | "paused" | "stopped";
  created_at: string;
}

export interface PingSample {
  ts: string;
  rtt_ms: number | null;
  ok: boolean;
}

// Punkt der Dauerping-Zeitreihe (/api/ping/jobs/<id>/series); count > 1 = gebündelt.
export interface PingSeriesPoint {
  ts: string;
  count: number;
  loss: number;
  rtt_min: number | null;
  rtt_avg: number | null;
  rtt_max: number | null;
}

export interface Capture {
  id: number;
  ts: string;
  iface: string;
  status: string;
  duration_s: number;
  max_packets: number | null;
  max_filesize_mb: number | null;
  ringbuffer: number | null;
  bpf: string | null;
  size_bytes: number | null;
  packet_count: number | null;
  error: string | null;
}

export interface PacketRow {
  number: string;
  time_epoch: string;
  src: string;
  dst: string;
  protocol: string;
  length: string;
  info: string;
}

export interface PacketList {
  total: number;
  offset: number;
  limit: number;
  packets: PacketRow[];
}

export interface IpConversation {
  endpoint_a: string;
  endpoint_b: string;
  frames_ab: number;
  bytes_ab: number;
  frames_ba: number;
  bytes_ba: number;
  frames_total: number;
  bytes_total: number;
}

export interface CaptureStatsBucket {
  t_epoch: number;
  packets_per_s: number;
  bits_per_s: number;
}

export interface CaptureStats {
  first_ts: number | null;
  last_ts: number | null;
  duration_s: number;
  total_packets: number;
  total_bytes: number;
  avg_pps: number;
  avg_bps: number;
  interval_s: number;
  series: CaptureStatsBucket[];
}

export interface FrameDistribution {
  total: number;
  unicast: number;
  broadcast: number;
  multicast: number;
  other: number;
  unicast_pct: number;
  broadcast_pct: number;
  multicast_pct: number;
  other_pct: number;
}

export interface TopProtocol {
  protocol: string;
  packets: number;
  bytes: number;
}

export interface TopConversation {
  addr_a: string;
  addr_b: string;
  packets: number;
  bytes: number;
}

export interface TcpHealth {
  tcp_packets: number;
  retransmissions: number;
  retransmission_pct: number;
  dup_acks: number;
  dup_ack_pct: number;
  resets: number;
}

export interface AnalysisWarning {
  level: "warn" | "info";
  code: string;
  message: string;
}

export interface CaptureAnalysis {
  packet_count: number | null;
  packets_analyzed: number;
  sampled: boolean;
  frame_distribution: FrameDistribution;
  top_protocols: TopProtocol[];
  top_conversations: TopConversation[];
  tcp_health: TcpHealth;
  warnings: AnalysisWarning[];
  protocol_hierarchy: { level: number; protocol: string; frames: number; bytes: number }[];
  top_endpoints: { address: string; packets: number; bytes: number }[];
  tcp_conversations: { endpoint_a: string; endpoint_b: string; bytes_total: number }[];
  udp_conversations: { endpoint_a: string; endpoint_b: string; bytes_total: number }[];
  dns_queries: { name: string; count: number }[];
  tls_sni: { server_name: string; count: number }[];
  tcp_retransmissions: number;
  tcp_resets: number;
  expert_infos: { severity: string; protocol: string; summary: string; count: number }[];
}

export interface CaptureJob {
  id: number;
  name: string;
  iface: string;
  bpf: string | null;
  max_file_size_mb: number;
  max_files: number;
  status: string;
  created_at: string;
  stopped_at: string | null;
  error: string | null;
}

export interface CaptureJobFile {
  name: string;
  size_bytes: number;
  mtime: string;
}

export interface Iperf3Result {
  id: number;
  ts: string;
  ok: boolean;
  host: string;
  port: number;
  protocol: string;
  duration_s: number;
  parallel: number;
  reverse: boolean;
  sent_mbps: number | null;
  recv_mbps: number | null;
  jitter_ms: number | null;
  lost_percent: number | null;
  lost_packets: number | null;
  packets: number | null;
  out_of_order: number | null;
  retransmits: number | null;
  error: string | null;
}

export interface Iperf3ServerStatus {
  running: boolean;
  port: number;
}

export interface MtuProbe {
  mtu: number;
  ok: boolean;
}

export interface MtuTestResult {
  id: number;
  ts: string;
  target: string;
  ok: boolean;
  path_mtu: number | null;
  min_mtu: number;
  max_mtu: number;
  probe_count: number;
  error: string | null;
  probes: MtuProbe[];
}

export interface VoipPortResult {
  port: number;
  ok: boolean;
  ms: number | null;
}

export interface VoipTest {
  id: number;
  server_id: number;
  ts: string;
  ok: boolean;
  source: string;
  sip_udp_ok: boolean | null;
  sip_tcp_ok: boolean | null;
  sip_tls_ok: boolean | null;
  webrtc_results: VoipPortResult[];
  sip_response_ms: number | null;
  sip_response_code: number | null;
  tls_handshake_ms: number | null;
  tls_verified: boolean | null;
  tls_cert_expiry: string | null;
  loss_pct: number | null;
  rtt_avg_ms: number | null;
  registration_ok: boolean | null;
  registration_detail: string | null;
  server_header: string | null;
  supported_methods: string | null;
  tls_version: string | null;
  tls_cipher: string | null;
  cert_subject: string | null;
  cert_issuer: string | null;
  capture_path: string | null;
  capture_url: string | null;
  sip_analysis: VoipSipAnalysis;
  codec_analysis: VoipCodecAnalysis;
  rtp_analysis: VoipRtpAnalysis;
  error: string | null;
}

export interface VoipSipAnalysis {
  messages_captured?: number;
  by_method?: Record<string, number>;
  by_status_class?: Record<string, number>;
  errors?: { code: number; reason: string; ts: string; src: string; dst: string }[];
  error_count?: number;
  retransmissions?: number;
  dialogs?: { call_id: string; methods: string[]; cseq_ok: boolean }[];
  server_headers?: string[];
  allow_methods?: string[];
  media_types?: string[];
  secure_rtp?: boolean;
}

export interface VoipCodecAnalysis {
  codecs_offered?: string[];
  primary_codec?: string | null;
  estimated_bandwidth_kbit?: number | null;
  ptime_ms?: number | null;
  secure?: boolean;
  directions?: string[];
}

export interface VoipRtpStream {
  ssrc: string;
  payload_type: string;
  codec: string;
  src: string;
  dst: string;
  packets: number;
  lost: number;
  lost_pct: number;
  max_jitter_ms: number;
  mean_jitter_ms: number;
  problems: string | null;
}

export interface VoipRtpAnalysis {
  streams_detected?: number;
  jitter_estimate_ms?: number | null;
  packet_loss_percent?: number | null;
  payload_types?: string[];
  total_packets?: number;
  streams?: VoipRtpStream[];
}

export interface VoipAnalysis {
  sip: VoipSipAnalysis;
  codecs: VoipCodecAnalysis;
  rtp: VoipRtpAnalysis;
  error: string | null;
}

export interface CaptureInterface {
  index: number;
  name: string;
  description: string | null;
}

export interface VoipServer {
  id: number;
  name: string;
  host: string;
  sip_port: number;
  sip_tls_port: number;
  webrtc_ports: string;
  sip_transport: "udp" | "tcp";
  check_registration: boolean;
  enabled: boolean;
  interval_s: number;
  created_at: string;
  last_test: VoipTest | null;
}

export interface TracerouteTarget {
  id: number;
  name: string;
  host: string;
  mode: "icmp" | "udp" | "tcp";
  tcp_port: number;
  max_hops: number;
  queries: number;
  resolve_geo: boolean;
  enabled: boolean;
  interval_s: number;
  created_at: string;
}

export interface TracerouteHop {
  hop_number: number;
  ip: string | null;
  hostname: string | null;
  latency_min_ms: number | null;
  latency_avg_ms: number | null;
  latency_max_ms: number | null;
  loss_pct: number | null;
  sent: number | null;
  mtu: number | null;
  country: string | null;
  city: string | null;
  asn: string | null;
  isp: string | null;
  lat: number | null;
  lon: number | null;
}

export interface Traceroute {
  id: number;
  ts: string;
  target_id: number | null;
  target: string;
  source: string;
  mode: string;
  engine: string;
  total_hops: number;
  completed: boolean;
  dest_reached: boolean;
  avg_latency_ms: number | null;
  total_loss_pct: number | null;
  error: string | null;
  hops: TracerouteHop[];
}

export type TracerouteListItem = Omit<Traceroute, "hops">;

export interface TracerouteCompareRow {
  hop_number: number;
  ip_a: string | null;
  ip_b: string | null;
  hostname_a: string | null;
  hostname_b: string | null;
  latency_avg_a: number | null;
  latency_avg_b: number | null;
  delta_ms: number | null;
  changed: boolean;
}

export interface TracerouteCompare {
  a: { id: number; ts: string; target: string };
  b: { id: number; ts: string; target: string };
  rows: TracerouteCompareRow[];
  path_changed: boolean;
}

export type HealthCheckType = "dns" | "http" | "smtp" | "imap" | "ntp";

export interface HealthCheckResult {
  id: number;
  probe_id: number;
  ts: string;
  ok: boolean;
  source: string;
  response_ms: number | null;
  status: string | null;
  details: Record<string, unknown>;
  error: string | null;
}

export interface HealthProbe {
  id: number;
  name: string;
  check_type: HealthCheckType;
  target: string;
  params: Record<string, unknown>;
  enabled: boolean;
  interval_s: number;
  created_at: string;
  last_result: HealthCheckResult | null;
}

export interface KeyMetric {
  label: string;
  value: string;
  sub: string;
  level: "ok" | "warn" | "info" | "na";
}

export interface Interpretation {
  level: "ok" | "warn" | "info";
  text: string;
}

export interface Report {
  id: number;
  ts: string;
  title: string;
  capture_id: number | null;
  job_id: number | null;
  retrans_test_id: number | null;
  customer: string;
  location: string;
  notes: string | null;
  sections: string[];
  has_pdf: boolean;
  error: string | null;
  html_url: string;
  pdf_url: string | null;
  key_metrics: KeyMetric[];
  interpretations: Interpretation[];
}

export interface ReportPreview {
  source: Record<string, string>;
  sections: string[];
  key_metrics: KeyMetric[];
  interpretations: Interpretation[];
}

export interface AuditEntry {
  id: number;
  ts: string;
  username: string | null;
  action: string;
  detail: string | null;
  ip: string | null;
}

export type RetransClassification =
  | "OK"
  | "LOCAL_NETWORK_ISSUE"
  | "ISP_OR_WAN_LINK_ISSUE"
  | "REMOTE_SERVER_ISSUE"
  | "UPLINK_CONGESTION"
  | "DOWNLINK_CONGESTION"
  | "UNKNOWN";

export interface RetransTarget {
  target_ip: string;
  target_port: number | null;
  is_private: boolean;
  retransmissions: number;
  packets: number;
  rate_percent: number;
  direction: "upload" | "download" | "both" | "unknown";
  first_seen: string | null;
  last_seen: string | null;
  connection_attempts: number | null;
  connection_errors: number | null;
  avg_connect_latency_ms: number | null;
}

export interface RetransTimelinePoint {
  t_offset_s: number;
  retrans_per_s: number;
  packets_per_s: number;
  rate_percent: number;
}

export interface RetransTest {
  id: number;
  ts: string;
  source: "manual" | "scheduled";
  interface: string;
  bpf: string | null;
  duration_s: number;
  test_mode: "passive" | "active";
  concurrent: boolean;
  status: "running" | "done" | "error";
  total_packets: number | null;
  tcp_packets: number | null;
  total_retransmissions: number | null;
  retransmission_rate_percent: number | null;
  classification: RetransClassification | null;
  recommendation: string | null;
  error: string | null;
}

export interface RetransConfiguredTarget {
  host: string;
  ip: string;
  port: number;
}

export interface RetransTestDetail extends RetransTest {
  targets: RetransTarget[];
  timeline: RetransTimelinePoint[];
  configured_targets: RetransConfiguredTarget[];
}

export interface RetransConfig {
  enabled: boolean;
  default_interface: string;
  default_duration_s: number;
  max_duration_s: number;
  warn_threshold_pct: number;
  alert_threshold_pct: number;
  scheduled_interval_min: number;
  max_active_targets: number;
}

export interface RetransTargetConfig {
  id: number;
  name: string;
  host: string;
  port: number;
  protocol: "tcp";
  description: string | null;
  created_at: string;
  last_used: string | null;
  usage_count: number;
}

export interface RetransCorrelation {
  window: { start: string; end: string };
  ping_failure_count: number;
  wan_failures: { target_id: number; ts: string; error: string | null }[];
  speedtests: { ts: string; ok: boolean; download_mbps: number | null; upload_mbps: number | null }[];
  correlated: boolean;
}

// --- Network Diagnostics ---
export interface DiagnosticTarget {
  name: string | null;
  host: string;
}

export interface DiagnosticTestSelection {
  ping: boolean;
  retrans: boolean;
  mtu: boolean;
  broadcast: boolean;
  traceroute: boolean;
  speedtest: boolean;
}

export interface DiagnosticPingResult {
  ok: boolean;
  loss_pct: number | null;
  rtt_min?: number | null;
  rtt_avg: number | null;
  rtt_max?: number | null;
  jitter_ms: number | null;
  error: string | null;
}

export interface DiagnosticRetransResult {
  rate_percent: number;
  retransmissions: number;
  packets: number;
  connection_errors: number | null;
}

export interface DiagnosticMtuResult {
  target: string;
  ok: boolean;
  path_mtu: number | null;
  min_mtu: number;
  max_mtu: number;
  probe_count: number;
  error: string | null;
}

export interface DiagnosticTracerouteResult {
  id: number;
  completed: boolean;
  dest_reached: boolean;
  total_hops: number;
  avg_latency_ms: number | null;
  first_hop: string | null;
  error: string | null;
}

export interface DiagnosticTargetResult {
  name: string;
  host: string;
  ping: DiagnosticPingResult | null;
  retrans: DiagnosticRetransResult | null;
  mtu: DiagnosticMtuResult | null;
  traceroute: DiagnosticTracerouteResult | null;
  status: "OK" | "WARN" | "KRITISCH";
  issues: string[];
}

export interface DiagnosticBroadcastResult {
  frame_distribution: FrameDistribution | null;
  warnings: AnalysisWarning[];
  error: string | null;
}

export interface DiagnosticSpeedtestResult {
  ok: boolean;
  download_mbps: number | null;
  upload_mbps: number | null;
  ping_ms: number | null;
  jitter_ms: number | null;
  error: string | null;
}

export interface DiagnosticResults {
  generated: string;
  interface: string;
  duration_s: number;
  tests: DiagnosticTestSelection;
  targets: DiagnosticTargetResult[];
  broadcast: DiagnosticBroadcastResult | null;
  speedtest: DiagnosticSpeedtestResult | null;
  retrans_test_id: number | null;
  retrans_error: string | null;
  overall_status: "OK" | "WARN" | "KRITISCH";
}

export interface DiagnosticRun {
  id: number;
  ts: string;
  interface: string;
  duration_s: number;
  status: "running" | "done" | "error";
  overall_status: "OK" | "WARN" | "KRITISCH" | null;
  targets: DiagnosticTarget[];
  tests: DiagnosticTestSelection;
  results: DiagnosticResults | null;
  error: string | null;
}

export interface DiagnosticPreset {
  id: number;
  name: string;
  targets: DiagnosticTarget[];
  tests: DiagnosticTestSelection;
  interface: string;
  duration_s: number;
  created_at: string;
}

export interface SchedulerJob {
  id: string;
  type: "speedtest" | "retransmission" | "wan" | "voip" | "traceroute" | "health";
  label: string;
  target: string | null;
  enabled: boolean;
  interval_seconds: number;
  paused: boolean;
  next_run: string | null;
  last_run: string | null;
  last_ok: boolean | null;
  page: string;
}

export interface SchedulerStatus {
  enabled: boolean;
  jobs: SchedulerJob[];
}

export interface DashboardHealth {
  status: string;
  tools: Record<string, boolean>;
  disk_free_mb: number;
  disk_used_pct: number;
  db_size_mb: number;
  captures_size_mb: number;
  active_ping_jobs: number;
}
