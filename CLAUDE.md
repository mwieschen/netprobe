# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

netprobe is a self-hosted LAN web appliance for network/WAN monitoring on Ubuntu Server
(22.04 / 24.04 / 26.04). Single-node, no cloud, no telemetry. Backend is a FastAPI monolith;
the APScheduler worker and the continuous-ping asyncio tasks run **inside the same process** as
the API. Frontend is a React SPA served as static files by the backend in production.

Language note: code comments, docstrings, log messages, API error strings, and docs are in
**German**. Match that when editing.

## Commands

Backend (`cd backend`, virtualenv activated):

```bash
pip install -e ".[dev]"
pytest                          # all tests
pytest tests/test_parsers.py    # one file
pytest tests/test_parsers.py::test_name -q   # one test
ruff check .                    # lint (line-length 110)
mypy app                        # type check
alembic upgrade head            # apply migrations
alembic revision --autogenerate -m "..."     # new migration (reads models via alembic/env.py)
python -m app.cli create-admin --username admin   # interactive password prompt
uvicorn app.main:app --reload --port 8000
```

Tests need no running DB: `tests/conftest.py` points config at a temp SQLite file and creates the
schema with `Base.metadata.create_all` **before** app modules import. `pytest-asyncio` is in
`auto` mode. The `admin` fixture returns a logged-in `TestClient`.

Frontend (`cd frontend`, Node ≥ 20.19):

```bash
npm install
npm run dev        # :5173, proxies /api -> 127.0.0.1:8000
npm run lint       # tsc --noEmit
npm run build      # tsc --noEmit && vite build -> frontend/dist
```

Deployment: `install.sh` is a single self-healing entry point (`curl …/install.sh | sudo bash`,
`sudo bash install.sh`, or `/opt/netprobe/install.sh` which it copies there). No prompts by default
(`--interactive` restores them; stdin is redirected so `curl | bash` is safe). Without a local source
dir it bootstraps one: latest GitHub release asset `netprobe.tar.gz` (prebuilt dist, from
`scripts/build-release.sh`) → else branch archive; `NETPROBE_REPO`/`NETPROBE_REF`/`NETPROBE_SOURCE_URL`
override. Mode = fresh install if `/opt/netprobe/venv` is missing, else smart update:
`plan_update()` sets `CHANGED_*` from `backend_code_changed()` (md5 of `BACKEND_PATHS`, source vs.
installed), `python_deps_changed()`, `frontend_code_changed()` (dist md5 for release packages,
source md5 vs. `.install-state/frontend-src.md5` for checkouts), `systemd_unit_changed()`,
`migrations_pending()` (`alembic current` vs. `heads`). Nothing changed → `self_heal()` only, no
restart. Otherwise `backup_current_installation()` (snapshot: DB `.backup` + `/opt/netprobe` sans
venv + config, under `/var/lib/netprobe/backups/snapshot_*`) → apply → migrate + verify → start →
smoke test; while `UPDATE_IN_PROGRESS=1` any failure triggers `on_exit()` → `restore_snapshot()`.
Flags: `--rebuild-frontend`, `--run-migrations`, `--backup-only`, `--rollback[=DIR]`,
`--list-backups`, `--force`. Initial admin password is random (shown once) unless
`NETPROBE_ADMIN_PASSWORD`. nginx/logrotate are dpkg-style conffiles (locally modified → kept, new
version written as `*.netprobe-new`). See `docs/installation.md`, `docs/update.md`.

## Architecture

### Config (`app/core/config.py`)
Single Pydantic-Settings `Settings` object, env prefix `NETPROBE_`, optionally loaded from
`/etc/netprobe/netprobe.env`. Import the module-level `settings` singleton everywhere; do not
instantiate `Settings()`. Runtime-tunable values (speedtest interval/enabled/server) are also
mirrored into the KV store and **mutated on the live `settings` object** by `settings_api.py` so
the scheduler picks them up without a restart.

### Time
All timestamps stored in **UTC**, timezone-aware, via a custom `UTCDateTime` type decorator
(`app/db/base.py`). Use `app.core.timeutils.utcnow()`; convert to `Europe/Berlin` only for display.

### DB / sessions (`app/db/session.py`)
SQLite in WAL mode (`foreign_keys=ON`, `busy_timeout=5000`). Two access patterns:
- `get_db()` — FastAPI dependency, does **not** auto-commit; endpoints call `db.commit()`.
- `session_scope()` — context manager for workers/CLI/background jobs; commits on exit, rolls back on exception.

Schema is managed by **Alembic** in production (`backend/alembic/versions/`). `app.cli init-db`
(`create_all`) exists but Alembic is authoritative — add a migration for any model change.

### Subprocesses (`app/services/proc.py`)
Every external tool call goes through `run_sync` / `run_async` here: **argument lists only,
never `shell=True`**, and `ensure_tool()` raises `ToolNotFoundError` (surfaced to the user as a
readable German message / HTTP 503). Host arguments are validated against a leading `-` and
passed after a `--` separator to prevent argument injection — keep this pattern when adding any
command that takes user input (`ping_manager._build_single_ping_args`, `wan_service._validate_host`).

Runtime privileges: the systemd unit runs with `NoNewPrivileges=true`, which **voids file
capabilities/setuid on `ping`, `fping`, `dumpcap`**. The service instead gets `CAP_NET_RAW` +
`CAP_NET_ADMIN` via `AmbientCapabilities` (see `deploy/systemd/netprobe.service`), and its
`HOME` is `/var/lib/netprobe` (the Ookla speedtest CLI needs a writable `~/.config`).
`ProtectSystem=strict` + `ReadWritePaths=/var/lib/netprobe /var/log/netprobe` means nothing
else on disk is writable. `scripts/fix-permissions.sh` is the idempotent repair for a
deployed box (capture dir perms, caps, speedtest license).

### Background work
- `app/workers/scheduler.py` — `AsyncIOScheduler` (UTC), one instance for every automated test
  (speedtest, retransmission monitor, per-target WAN/VoIP/traceroute/health jobs). Scheduled
  speedtests use a DB-based job lock via `kvstore.acquire_lock`; blocking jobs are dispatched with
  `asyncio.to_thread`. `reschedule_speedtest()` / `sync_wan_jobs()` / `sync_voip_jobs()` /
  `sync_traceroute_jobs()` / `sync_health_jobs()` / `reschedule_retransmission()` re-sync jobs
  after config or target changes. `SchedulerService.enabled` is a global automation switch
  (`/api/scheduler/enable|disable`, mirrored in the KV store as `scheduler_enabled`, same
  live-reload pattern as `speedtest_enabled`): it pauses/resumes every test job via APScheduler's
  own `job.pause()`/`job.resume()` without removing them, applied via `_apply_enabled_state()` at
  the end of every sync/reschedule method so newly-added jobs come up already paused if the switch
  is off. `_HOUSEKEEPING_JOB_IDS` (`ping_aggregate`, `retention_cleanup`) are exempt — they always
  run. `app/api/scheduler_api.py` (`/api/scheduler`) builds the `GET /status` overview by walking
  every target table (`WanTarget`, `VoipServer`, `TracerouteTarget`, `HealthProbe`) plus the two
  global jobs, cross-referencing `scheduler.get_job(id)` for next-run/paused state and each type's
  own latest result row for last-run/last-ok — there is deliberately no separate history table.
  Per-target intervals stay editable only on each feature's own page (`WanTarget.interval_s` etc.);
  the Scheduler page is a read-only overview to avoid a second, conflicting config surface.
- `app/services/ping_manager.py` — one asyncio task per running `PingJob`. `bind_loop()` in the
  lifespan lets synchronous endpoints schedule tasks onto the main loop via
  `call_soon_threadsafe`. `resume_all()` on startup restarts jobs marked `running` in the DB.
- `app/services/long_capture.py` (`long_capture_manager`) — one long-lived `dumpcap -q` child
  per running `CaptureJob`, ring-buffered into `<capture_dir>/<job_id>/`. `_watch()` reads the
  child's stderr to EOF and marks the job `error` if it exits while still `running`. `resume_all()`
  on startup. Rotated files are read from disk, not tracked in the DB.
- `app/services/iperf_service.py` (`iperf3_server`) — manages a single `iperf3 -s` child; the
  desired `{enabled, port}` lives in the KV store and is restored by `resume()` on startup.
- The lifespan (`app/main.py`) binds the loop and calls `resume_all()`/`resume()` for all three
  managers, and shuts them down in `finally`.
- `app/services/events.py` — in-process SSE broker (`ping_broker`); slow consumers drop oldest.
  Ping samples stream to the UI at `/api/ping/stream`.

### Retention model
`ping_samples` raw for 14 days → rolled into 5-minute `ping_aggregates` (365 days).
`speedtest_results` / `wan_results` kept `result_retention_days` (365). `captures` deleted after
`capture_retention_days` (14).

### API layer (`app/api/`)
Routers each own their `/api/<area>` prefix and are registered in `app/main.py`. Auth is a
signed HttpOnly session cookie (`itsdangerous` timestamp signer); `deps.py` provides
`get_current_user` and `require_admin` (RBAC: `admin` full, `viewer` read-only — all mutating
endpoints require admin). `client_ip()` only trusts `X-Forwarded-For` from loopback proxies.
Login rate limiting is in-memory with **two keys** (`ip:<ip>:<user>` and `user:<user>`) so a
spoofed source IP can't bypass it. Security-relevant actions are written to `audit_log` via
`app.services.audit.record`.

### SPA serving (`app/main.py`)
When `frontend/dist` exists, `/assets` is mounted and a catch-all route serves files with an
explicit path-traversal check (resolved path must stay within `dist`), falling back to
`index.html`. `/api/*` paths return JSON 404 from the catch-all.

### Captures (`app/services/capture_service.py`, `app/api/captures.py`)
`dumpcap` records to `settings.capture_dir` (outside the webroot); `tshark` analyzes.
`clamp_limits()`, `validate_iface()`, `validate_bpf()` bound user input; display filters are
capped at `display_filter_max_packets` (1000). Every file path is re-checked with
`relative_to(settings.capture_dir)` before download/delete. Analysis extras:
`apply_display_filter()` (empty filter = all packets), `conversations_ip()`, `capture_stats()`
(time-bucketed pps/bps via `tshark_parsers.compute_io_buckets`) — served under
`GET /api/captures/<id>/{packets,conversations,stats}` and the `/captures/:id` analysis page.

`capture_service.analyze(path, max_packets=None)` also returns `frame_distribution`
(unicast/broadcast/multicast via a single `-T fields` pass → `tshark_parsers.parse_capture_metrics`),
`top_protocols`, `top_conversations`, `tcp_health`, and `warnings` (thresholds are
`settings.analysis_*`). It caps every tshark pass at `settings.analysis_max_packets` (`-c`) and
sets `sampled` / `packet_count` (true count via `capinfos`).

### Long-term captures & other tools
- `app/api/capture_jobs.py` (`/api/capture-jobs`) + `app/models/capture_job.py` — see
  `long_capture_manager` above. Rotated files listed/downloaded via `resolve_job_file()`
  (path-validated against the job dir).
- `app/api/iperf3.py` (`/api/iperf3`) — `iperf_service.run_client()` parses `iperf3 -J`
  (`parsers.parse_iperf3_json`); results persisted to `iperf_results`.
- `app/api/mtu.py` (`/api/mtu-test`) — `mtu_service.discover_path_mtu()` binary-searches path
  MTU with `ping -4 -M do -s <mtu-28>`; results persisted to `mtu_tests`.
- `app/api/reports.py` (`/api/reports`) + `app/models/report.py` — `report_service.generate()`
  runs `analyze()` + gathers latest test results, renders a hand-rolled print HTML template
  (everything `html.escape`d), writes `<settings.reports_dir>/<id>.html`, and optionally a PDF
  via **WeasyPrint** (lazy import; not a pyproject dep — the installer `pip install`s it into
  the venv, code degrades to HTML-only if missing). Files served by `download_report` are
  path-derived from the id, HTML inline / PDF attachment.
- `app/api/voip.py` (`/api/voip`) + `app/models/voip.py` — `voip_service.run_test()` checks
  3CX/SIP reachability with **stdlib only** (`socket`/`ssl`): SIP `OPTIONS` ping (UDP+TCP),
  SIP-over-TLS handshake timing + cert expiry, WebRTC/HTTPS TCP ports, ICMP quality (reuses
  `wan_service.measure`), and an optional Digest-auth `REGISTER` (credentials come only from
  the request body — **never persisted or logged**; TLS `REGISTER` requires a verified cert).
  Scheduled per server via `scheduler_service.sync_voip_jobs()` (`voip_server_` job prefix).
  Never places a real call. `sip_options_ping` also parses response headers
  (`server`/`allow`); `check_tls` returns version/cipher/subject/issuer.
- `app/services/voip_analysis.py` + `app/services/sip_parsers.py` (pure, like
  `tshark_parsers`) — optional PCAP deep-analysis. A test with `capture_interface`
  runs `capture_service.run_capture_window()` in a thread parallel to the probes,
  then `analyze_capture()` does two tshark passes (`-Y sip -T fields`; `-z rtp,streams`)
  → `parse_sip_messages` (dialogs, 4xx/5xx/6xx, retransmissions), `codec_summary`
  (SDP rtpmap/fmtp/ptime + static payloads, SRTP, rough bandwidth), `parse_rtp_streams`
  + `rtp_summary` (jitter/loss). `SIP_FIELDS` order MUST match `sip_parsers._N_FIELDS`.
  `POST /api/voip/analyze` runs it on any existing capture (path checked against
  `capture_dir`); `GET /api/voip/tests/<id>/capture` downloads the run's pcap.
  CLI: `voip-analyze --capture-file`.
- `app/api/traceroute.py` (`/api/traceroute`) + `app/models/traceroute.py` —
  `traceroute_service.run()` prefers `mtr --json` (`parse_mtr_json`), falls back to
  `traceroute` text (`parse_traceroute_text`); enriches hops with reverse DNS,
  optional path-MTU (`mtu_service`), and geo/ASN via `geoip_service.lookup()`.
  `geoip_service` is **hybrid**: cache (`geoip_cache` table) → local MaxMind `.mmdb`
  (`maxminddb`, base dep) → ip-api.com HTTP batch (`geoip_online_*` settings,
  in-memory rate limit). Private/bogon IPs are never sent externally — this is the
  one documented exception to "no cloud" (`docs/security.md`). Scheduled via
  `sync_traceroute_jobs()` (`traceroute_target_` prefix). `Traceroute` has no FK to
  its target (survives target deletion, like `Report`). `compare()` diffs two runs.
- `app/api/health_checks.py` (`/api/health-checks`) + `app/models/health_check.py` —
  a small probe framework in `app/services/health/` (`__init__.py` holds the
  `register()`/`run_check()`/`run_probe()` registry; submodules `dns`, `http`,
  `smtp_imap`, `ntp` self-register on import). All checks are **stdlib only**
  (hand-rolled DNS query, SNTP packet, `smtplib`/`imaplib` handshake — never a
  login), each returns a `HealthOutcome`. `HealthProbe.params_json` carries
  type-specific params. Scheduled via `sync_health_jobs()` (`health_probe_` prefix).
  CLI: `dns-test`, `health-check`.
- `app/api/retransmission.py` (`/api/retransmission`) + `app/models/retransmission.py`
  (`RetransmissionTest`/`RetransmissionTarget`/`RetransmissionTimelinePoint`/
  `RetransmissionTargetConfig`) — `app/services/retransmission_service.py` supports two modes.
  **Passive** (default): a short `dumpcap` window (via `capture_service.run_capture_window`)
  analyzed with one `tshark -Y tcp` pass (`tcp.analysis.retransmission`); targets are
  auto-discovered from observed retransmissions. **Active**: up to `MAX_ACTIVE_TARGETS` (10)
  free-form `{host, port}` targets are resolved once (`resolve_active_target`, stdlib
  `socket.gethostbyname` — same resolved IP feeds both the BPF filter and the connect probes, so
  DNS round-robin can't split them) and TCP-connect-probed (`_probe_loop`/`_tcp_connect_probe`,
  stdlib `socket` only, no payload — just the handshake) by a `_CaptureThread` running the capture
  in parallel (same pattern as `voip_service._CaptureThread`), either concurrently
  (`ThreadPoolExecutor`, full `duration_s` each) or sequentially (`duration_s` split evenly across
  targets). TCP only — UDP is rejected (`SUPPORTED_ACTIVE_PROTOCOLS`), since
  `tcp.analysis.retransmission` is TCP-specific and UDP loss is already covered by iperf3.
  Active mode checks `ensure_tool(settings.dumpcap_bin)` immediately after creating the test row
  and before starting any probing — fail fast on a missing tool instead of burning the full
  `duration_s` on probes whose capture can never succeed. In both modes the pcap is deleted after
  analysis, only the aggregation is persisted. Pure functions at the top of the file
  (`parse_retransmission_lines`, `aggregate_retransmissions`, `compute_timeline`, `classify`) are
  unit-testable without IO; `aggregate_retransmissions`' `configured` param (list of resolved
  `(ip, port)`) switches it from passive auto-discovery (only targets with ≥1 retransmission) to
  active mode (every configured target appears, including 0/0 if no traffic was ever captured for
  it — placeholder entries are matched by IP only, not `(ip, port)`, to avoid duplicate rows when
  the real port differs). `classify()` labels the likely cause (`LOCAL_NETWORK_ISSUE` /
  `ISP_OR_WAN_LINK_ISSUE` / `REMOTE_SERVER_ISSUE` / `UPLINK_CONGESTION` / `DOWNLINK_CONGESTION` /
  `UNKNOWN` / `OK`) from which affected targets are private vs. public IPs; direction
  (upload/download) needs the interface's own IPv4 (best-effort via `ip -4 addr show`, falls back
  to `unknown`). Threshold breaches (`retrans_warn_threshold_pct`/`retrans_alert_threshold_pct`)
  only log + audit locally — intentionally no outbound webhook/email, to keep the "no cloud"
  principle. Optional single periodic job via `scheduler_service.reschedule_retransmission()`
  (`retrans_scheduled_interval_min`, 0 = off) on `capture_service.resolve_default_interface()` (`retrans_default_interface`, default `auto` = default-route iface; a missing/down configured iface also falls back to it — never read the raw setting as an interface) — scheduled runs are
  always passive; active mode is manual/CLI-only for now. Report export reuses `report_service`
  (`Report.retrans_test_id`, section `"retransmission"`, extra connection-stat columns when
  `test_mode == "active"`) rather than a separate generator. `RetransmissionTargetConfig` rows are
  saved target presets (reused across tests, `usage_count`/`last_used` bumped by
  `api.retransmission._touch_target_configs` on active-test start by host+port match). CLI:
  `retrans-test` (`--mode`, `--target`/`--targets`/`--target-id`, `--sequential`), `retrans-report`,
  `retrans-monitor`, `retrans-target-add`/`-list`/`-delete`. See `docs/retransmissions.md`.
- `app/api/diagnostics.py` (`/api/diagnostics`) + `app/models/diagnostics.py`
  (`DiagnosticRun`/`DiagnosticPreset`) + `app/services/diagnostics_service.py` — Network
  Diagnostics is a thin **orchestrator**, not a new test engine: every sub-test calls the
  already-tested service directly (`wan_service.measure`, `mtu_service.discover_path_mtu`,
  `traceroute_service.run`, `speedtest_service.run_speedtest`, `capture_service.analyze` for the
  broadcast share, `retransmission_service.run_test(mode="active", targets=[...])` for TCP
  retransmissions). Multiple diagnostic targets' retransmission checks are combined into **one**
  active-mode call/capture (it already supports multiple targets) rather than one capture per
  target. Sub-tests run concurrently via `ThreadPoolExecutor`; each DB-writing sub-test opens its
  own `session_scope()` inside its worker function (a `Session` isn't thread-safe to share). Ping
  results are never persisted as `WanResult` (that table's `target_id` is a NOT NULL FK to a saved
  `WanTarget`, which ad-hoc diagnostic targets don't have) — they only live in the run's
  `results_json` snapshot; MTU/Traceroute/Speedtest results persist normally in their own tables
  (same construction as their respective API endpoints) and so also show up in those features'
  own history. `classify_target()`/`classify_run()` are pure functions reusing the *existing*
  threshold settings (`report_ping_loss_warn_pct`, `report_jitter_warn_ms`,
  `retrans_warn_threshold_pct`/`retrans_alert_threshold_pct`, `report_mtu_info_below`,
  `analysis_broadcast_warn_pct`) rather than a second, potentially-diverging threshold source;
  only `diagnostics_ping_latency_warn_ms` is new (nothing else covered ping latency). A
  completely failed retransmission sub-test (e.g. missing `dumpcap` — `RetransmissionTest` created
  with `status="error"` and zero `RetransmissionTarget` rows) is surfaced as `results["retrans_error"]`
  and escalates the overall status, instead of silently vanishing — a real bug caught via live
  browser testing where a user-selected test produced no visible result on failure. No separate
  history table per sub-test; `DiagnosticRun.results_json` is a JSON snapshot (like
  `Report.data_json`) — sub-test results stay queryable in their own tables/pages. CLI:
  `diagnostics-run` (`--target`, `--tests`), `diagnostics-preset-add`/`-list`/`-delete`. See
  `docs/diagnostics.md`.
- Every new external-tool call keeps the `proc.py` discipline (arg list, `--` separator,
  host regex, no leading `-`). Use the shared `proc.validate_network_target()` for new host
  inputs. `iperf3` is added to the installer's apt list; the optional
  always-on server unit is `deploy/systemd/netprobe-iperf3.service` (installed disabled).
- CLI is a package: `app/cli/__init__.py` (`main` dispatcher) + `__main__.py` + per-area
  modules (`admin.py`, `voip.py`, `traceroute.py`, `health.py`). Entry point stays
  `app.cli:main`; `python -m app.cli` works.

### Frontend (`frontend/src/`)
Vite + React 19 + react-router 7, ECharts used via the direct API in `components/Chart.tsx`
and Leaflet likewise in `components/MapView.tsx` (no wrapper lib — needed for React 19;
`MapView` loads OpenStreetMap tiles at runtime from the viewer's browser). `api.ts` is the single fetch client (`credentials:
"include"`); `auth.tsx` holds the auth context; one page component per nav route in `pages/`.
No test runner configured for the frontend — `npm run lint` / `npm run build` are the gates.

## Conventions

- ruff line length 110, target py312; mypy with the pydantic plugin, `disallow_untyped_defs` off.
- Keep new subprocess calls behind `proc.py` with validated, `--`-separated arguments.
- New persisted fields → SQLAlchemy model + Alembic migration + Pydantic schema in
  `app/schemas/schemas.py` + the mirrored TypeScript `interface` in `frontend/src/api.ts`.
- Never log passwords or secrets; route security-relevant events through the audit log.
