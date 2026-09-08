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
