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
  error: string | null;
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

export interface DashboardHealth {
  status: string;
  tools: Record<string, boolean>;
  disk_free_mb: number;
  disk_used_pct: number;
  db_size_mb: number;
  captures_size_mb: number;
  active_ping_jobs: number;
}
