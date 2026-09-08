# netprobe – Architektur

`netprobe` ist eine lokale Web-Appliance für Netzwerk- und WAN-Monitoring, die im
LAN über einen Browser administriert wird. Dieses Dokument beschreibt die
Gesamtarchitektur, das Datenmodell, das Sicherheitsmodell und die Betriebsweise.

## 1. Überblick

```
                         ┌──────────────────────────────────────────┐
   Browser (LAN)  ──────▶│  nginx :80/:443  (Reverse Proxy, TLS)     │
                         └───────────────┬──────────────────────────┘
                                         │ proxy_pass 127.0.0.1:8000
                         ┌───────────────▼──────────────────────────┐
                         │  uvicorn/gunicorn (loopback only)          │
                         │  FastAPI-App  (Benutzer: netprobe)         │
                         │  ┌──────────────┐   ┌────────────────────┐ │
                         │  │ REST/OpenAPI │   │ APScheduler-Worker │ │
                         │  │ + SSE-Stream │   │ (im selben Prozess)│ │
                         │  └──────┬───────┘   └─────────┬──────────┘ │
                         └─────────┼─────────────────────┼────────────┘
                                   │                     │
                         ┌─────────▼─────────────────────▼──────────┐
                         │  SQLite (WAL)  /var/lib/netprobe/db.sqlite │
                         └───────────────────────────────────────────┘
                                   │
              ┌────────────────────┼─────────────────────────────┐
              ▼                    ▼                              ▼
     speedtest (Ookla CLI)   fping / ping              dumpcap / tshark
```

Alle Messungen laufen als Subprozesse mit **Argumentlisten** (`shell=False`,
niemals `shell=True`). Benutzereingaben werden nie an eine Shell übergeben.

## 2. Technologie-Stack

| Schicht        | Technologie                                             |
|----------------|---------------------------------------------------------|
| Backend        | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic |
| Scheduler      | APScheduler (AsyncIOScheduler, im API-Prozess)          |
| Datenbank      | SQLite mit WAL-Modus                                     |
| Auth           | Argon2 (argon2-cffi), signierte HttpOnly-Session-Cookies |
| Frontend       | React + TypeScript + Vite, ECharts                      |
| Messwerkzeuge  | Ookla Speedtest CLI, fping/ping, dumpcap, tshark        |
| Reverse Proxy  | nginx (Port 80/443)                                     |
| Prozess-Mgmt   | systemd, logrotate                                      |

Der Scheduler-Worker läuft **im selben Prozess** wie die API (AsyncIOScheduler).
Das vereinfacht Deployment und Job-Locking und ist für eine Single-Node-Appliance
ausreichend. Job-Locks werden zusätzlich in der DB gehalten, damit auch mehrere
Gunicorn-Worker sicher sind (Standard: ein Worker).

## 3. Datenmodell

Alle Zeitstempel werden **intern in UTC** gespeichert (`DateTime`, timezone-aware)
und erst in der UI nach `Europe/Berlin` konvertiert.

- **users** – `id, username, password_hash (argon2), role (admin|viewer), is_active, created_at`
- **audit_log** – `id, ts, user_id, username, action, detail, ip`
- **app_settings** – Key/Value-Store (`key, value_json`) für Laufzeit-Konfiguration
- **speedtest_results** – `id, ts, ok, download_mbps, upload_mbps, ping_ms, jitter_ms, packet_loss, server_id, server_name, error, source (manual|scheduled)`
- **wan_targets** – `id, name, host, interval_s, count, timeout_ms, enabled`
- **wan_results** – `id, target_id, ts, ok, loss_pct, rtt_min, rtt_avg, rtt_max, rtt_stddev, jitter_ms, error`
- **ping_jobs** – `id, name, host, interval_s, status (running|paused|stopped), created_at`
- **ping_samples** – `id, job_id, ts, rtt_ms (nullable = timeout), ok` (Rohdaten, 14 Tage)
- **ping_aggregates** – `id, job_id, bucket_ts (5-Min), count, loss, rtt_min, rtt_avg, rtt_max` (Langzeit)
- **captures** – `id, ts, iface, status, duration_s, max_packets, max_filesize_mb, ringbuffer, bpf, path, size_bytes, packet_count, error`

### Retention / Aggregation

- **ping_samples**: Rohdaten 14 Tage. Ein periodischer Job bildet 5-Minuten-Aggregate
  (`ping_aggregates`), die deutlich länger (Standard 365 Tage) behalten werden.
- **speedtest_results / wan_results**: konfigurierbare Aufbewahrung (Standard 365 Tage).
- **captures**: nach `capture_retention_days` (Standard 14) gelöscht.

## 4. Messverfahren

### 4.1 Speedtest
- Aufruf: `speedtest --format=json --accept-license --accept-gdpr [--server-id N]`.
- JSON wird geparst; gespeichert werden Download/Upload (Mbps aus Bytes/s × 8 / 1e6),
  Ping, Jitter, Packet Loss, Server-ID und -Name.
- **Job-Lock**: DB-Zeile `app_settings["speedtest_lock"]`; ein laufender Test blockiert
  parallele Starts (manuell wie geplant).

### 4.2 WAN-Qualität
- Aufruf bevorzugt `fping -q -C <count> -p <period> -t <timeout> <host>`, Fallback `ping`.
- Aus den RTT-Werten werden min/avg/max, Standardabweichung und **Jitter** berechnet.
- **Jitter** = Mittelwert der absoluten Differenzen aufeinanderfolgender RTTs
  (`mean(|rtt[i] - rtt[i-1]|)`).
- Paketverlust in Prozent; Down-/Timeout-Events werden als `ok=False` gespeichert.

### 4.3 Dauerping
- Pro Job ein asynchroner Task, der in `interval_s`-Abständen einen einzelnen Ping
  absetzt und ein `ping_sample` schreibt.
- Live-Updates via **Server-Sent Events** (`/api/ping/stream`).
- Status Start/Stop/Pause/Löschen über die API.

### 4.4 Packet Capture
- Capture: `dumpcap -i <iface> -a duration:<s> -c <packets> -b filesize:<kb> -b files:<n> [-f <bpf>] -w <file>`.
- Analyse: `tshark -r <file> -q -z io,phs`, `-z endpoints,ip`, `-z conv,tcp`, `-z conv,udp`,
  `-z expert`, sowie Feld-Extraktion für DNS-Queries, TLS-SNI, TCP-Retransmissions, TCP-Resets.
- Display-Filter: `tshark -r <file> -Y <filter> -T json` mit Timeout, Paging und Limit 1000.
- Dateien liegen außerhalb des Webroots unter `/var/lib/netprobe/captures`.

## 5. Sicherheitsmodell

Siehe `docs/security.md`. Kernpunkte:
- Betrieb als nicht-root-Benutzer `netprobe`.
- Uvicorn nur auf Loopback; nginx terminiert LAN-Traffic.
- Argon2-Passwort-Hashes, signierte HttpOnly-Cookies, `SameSite=Lax`, `Secure` bei HTTPS.
- RBAC: `admin` (Vollzugriff) und `viewer` (nur Lesen).
- Rate Limiting für Login.
- Audit-Log für sicherheitsrelevante Aktionen.
- Packet Capture nur über minimale Capabilities (`dumpcap`-Gruppe/Setcap), nicht als root.
- Keine Cloud-Dienste, keine Telemetrie.

## 6. API-Struktur

Alle Endpunkte unter `/api`, dokumentiert via OpenAPI (`/api/docs`).

| Bereich       | Präfix                | Auth      |
|---------------|-----------------------|-----------|
| Health        | `/api/health`         | öffentlich |
| Auth          | `/api/auth`           | gemischt  |
| Speedtest     | `/api/speedtest`      | viewer/admin |
| WAN           | `/api/wan`            | viewer/admin |
| Dauerping     | `/api/ping`           | viewer/admin |
| Captures      | `/api/captures`       | viewer/admin |
| Einstellungen | `/api/settings`       | admin     |
| Benutzer      | `/api/users`          | admin     |
| Audit         | `/api/audit`          | admin     |

Schreibende Aktionen (Tests starten, Konfiguration, Captures) erfordern `admin`.

## 7. Deployment

- **systemd**: `netprobe.service` startet Gunicorn/Uvicorn als `netprobe` auf `127.0.0.1:8000`.
- **nginx**: Reverse Proxy auf Port 80, vorbereitete TLS-Server-Section für 443.
- **logrotate**: rotiert `/var/log/netprobe/*.log`.
- **Backups**: `scripts/backup.sh` nutzt `sqlite3 .backup` / `VACUUM INTO`.
- Konfiguration ausschließlich über `/etc/netprobe/netprobe.env`.

## 8. Verzeichnis-Layout (Laufzeit)

```
/opt/netprobe/            Anwendung (Backend-Code, venv, Frontend-Build)
/etc/netprobe/netprobe.env  Konfiguration
/var/lib/netprobe/db.sqlite Datenbank
/var/lib/netprobe/captures  PCAPNG-Dateien
/var/log/netprobe/          Logs
```
