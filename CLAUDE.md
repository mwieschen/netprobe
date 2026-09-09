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

Full deployment is `sudo ./install.sh` (idempotent; detects OS, builds frontend, sets up
systemd + nginx, creates admin). See `docs/operations.md` for update/backup/restore.

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
- `app/workers/scheduler.py` — `AsyncIOScheduler` (UTC). Scheduled speedtests (DB-based job lock
  via `kvstore.acquire_lock`), per-target WAN measurements, 5-min ping aggregation, retention
  cleanup. Blocking jobs are dispatched with `asyncio.to_thread`. `reschedule_speedtest()` /
  `sync_wan_jobs()` re-sync jobs after config or target changes.
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
  Never places a real call.
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
- Every new external-tool call keeps the `proc.py` discipline (arg list, `--` separator,
  host regex, no leading `-`). Use the shared `proc.validate_network_target()` for new host
  inputs. `iperf3` is added to the installer's apt list; the optional
  always-on server unit is `deploy/systemd/netprobe-iperf3.service` (installed disabled).
- CLI is a package: `app/cli/__init__.py` (`main` dispatcher) + `__main__.py` + per-area
  modules (`admin.py`, `voip.py`, `traceroute.py`). Entry point stays `app.cli:main`;
  `python -m app.cli` works.

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
