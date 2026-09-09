# netprobe – Betrieb: Update, Backup, Restore, Fehleranalyse, Deinstallation

## Update

netprobe wird durch erneutes Ausrollen des Quellcodes aktualisiert. Das
Installationsscript ist idempotent und zerstört weder Datenbank noch Konfiguration.

```bash
# Im entpackten/aktualisierten Repository:
sudo ./install.sh
```

Alternativ manuell:

```bash
sudo systemctl stop netprobe
sudo cp -r backend/app /opt/netprobe/backend/
sudo cp -r backend/alembic /opt/netprobe/backend/
sudo /opt/netprobe/venv/bin/pip install /opt/netprobe/backend
# Frontend neu bauen und nach /opt/netprobe/frontend/dist kopieren
sudo -u netprobe env NETPROBE_ENV_FILE=/etc/netprobe/netprobe.env \
    /opt/netprobe/venv/bin/alembic -c /opt/netprobe/backend/alembic.ini upgrade head
sudo systemctl start netprobe
```

Migrationen werden über Alembic eingespielt (`alembic upgrade head`).

## Backup

Die SQLite-Datenbank wird konsistent mit `sqlite3 .backup` gesichert (WAL-sicher):

```bash
sudo /opt/netprobe/backend/../scripts/backup.sh          # Standardziel: /var/lib/netprobe/backups
# oder mit Repo-Skript:
sudo ./scripts/backup.sh /pfad/zum/backupordner
```

Das Skript erzeugt eine komprimierte Kopie `netprobe_<zeitstempel>.sqlite.gz` und
räumt Backups älter als 30 Tage auf.

**Automatisieren** (z. B. tägliches Backup um 03:30 Uhr) via cron:

```bash
echo '30 3 * * * root /opt/netprobe/scripts/backup.sh' | sudo tee /etc/cron.d/netprobe-backup
```

Alternativ innerhalb von SQLite: `VACUUM INTO '/pfad/backup.sqlite';`

## Restore

```bash
sudo ./scripts/restore.sh /var/lib/netprobe/backups/netprobe_20260101_033000.sqlite.gz
```

Das Skript stoppt den Dienst, prüft die Integrität (`PRAGMA integrity_check`),
sichert die bestehende DB und spielt das Backup ein, danach Neustart.

## Fehleranalyse

| Symptom                                   | Vorgehen                                                        |
|-------------------------------------------|----------------------------------------------------------------|
| Weboberfläche nicht erreichbar            | `systemctl status netprobe` und `systemctl status nginx`       |
| Dienst startet nicht                      | `journalctl -u netprobe -n 100 --no-pager`                     |
| 502 Bad Gateway von nginx                 | Backend prüfen: `curl http://127.0.0.1:8000/api/health`        |
| Speedtest schlägt fehl                    | `command -v speedtest`; als Dienst-User testen: `sudo -u netprobe env HOME=/var/lib/netprobe speedtest --accept-license --accept-gdpr` |
| Packet Capture „Permission denied“ beim Schreiben | `stat -c '%U:%G %a' /var/lib/netprobe/captures` → erwartet `netprobe:wireshark 2770`; sonst `sudo ./scripts/fix-permissions.sh` |
| Packet Capture „dumpcap fehlt/keine Rechte“ | `getcap $(command -v dumpcap)`; Dienst-`CapEff` prüfen (s. u.) |
| Keine WAN-Messwerte / „Operation not permitted“ | `getcap $(command -v fping)`; Dienst braucht `AmbientCapabilities=CAP_NET_RAW` (s. u.) |
| Dauerping ohne Daten                       | Job-Status „running“? `journalctl -u netprobe \| grep Dauerping`; ggf. `fix-permissions.sh` |
| SSE/Live-Ansicht aktualisiert nicht        | nginx-Buffering für `/api/ping/stream` deaktiviert? (Config)   |

### Nicht-root-Messungen (Capabilities)

Ping, fping und dumpcap benötigen `CAP_NET_RAW` (dumpcap zusätzlich `CAP_NET_ADMIN`).
Weil der systemd-Dienst mit `NoNewPrivileges=true` läuft, greifen die File-Capabilities
der Binaries dort **nicht**. Die Capabilities werden deshalb im Unit direkt an den
Dienst vergeben:

```ini
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
```

Prüfen, ob der laufende Dienst die Capabilities hat:

```bash
PID=$(systemctl show -p MainPID --value netprobe)
grep Cap /proc/$PID/status
# CapEff sollte Bit 12 (CAP_NET_ADMIN) und Bit 13 (CAP_NET_RAW) enthalten -> 0x3000
capsh --decode=$(awk '/CapEff/{print $2}' /proc/$PID/status)
```

Fehlt es: Unit aus `deploy/systemd/netprobe.service` neu einspielen und
`systemctl daemon-reload && systemctl restart netprobe` – oder einfach
`sudo ./scripts/fix-permissions.sh` ausführen.

### scripts/fix-permissions.sh

Idempotentes Reparaturskript für genau diese drei Probleme (Capture-Rechte,
Ping-/fping-Capabilities, Speedtest-HOME/Lizenz). Aus dem Repo-Verzeichnis:

```bash
sudo ./scripts/fix-permissions.sh
```

Healthcheck-Endpunkt: `curl http://<server>/api/health` → `{"status":"ok"}`.

Werkzeug-Verfügbarkeit wird im Dashboard unter *Werkzeuge* angezeigt.

### Logs

- Dienst/Anwendung: `journalctl -u netprobe -f`
- Datei-Log: `/var/log/netprobe/netprobe.log` (rotiert via logrotate)
- nginx: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`

## Analyse- & Test-Features (Endpoints & UI)

### 1. Erweiterte Direkt-Analyse gespeicherter Captures

Zusätzlich zu `GET /api/captures/<id>/analysis` (Basisanalyse) und
`POST /api/captures/<id>/filter`:

| Endpoint | Zweck |
|----------|-------|
| `GET /api/captures/<id>/analysis` | Grundanalyse (s. u.). |
| `GET /api/captures/<id>/packets?filter=&offset=&limit=` | Gefilterte Paketliste (JSON). `filter` = tshark-Display-Filter (leer = alle). Ergebnis auf `NETPROBE_DISPLAY_FILTER_MAX_PACKETS` (Standard 1000) begrenzt, Paging über `offset`/`limit`. |
| `GET /api/captures/<id>/conversations` | IP-Conversations (`tshark -q -z conv,ip`), sortiert nach Volumen. |
| `GET /api/captures/<id>/stats?buckets=` | I/O-Statistik über die Zeit: Pakete/s und Bit/s je Intervall plus Gesamtwerte (`buckets` 10–500, Standard 120). |

Alle benötigen eine Anmeldung (viewer genügt), laufen `shell=False` über
`tshark` und werden im Threadpool ausgeführt.

**`/analysis`** liefert zusätzlich zur bestehenden Basisanalyse
(Protokollhierarchie, DNS, TLS-SNI, Expert-Infos):

- `frame_distribution` – Unicast / Broadcast / Multicast / „ohne Ethernet" in
  Prozent (klassifiziert über `eth.dst.ig` bzw. Broadcast-MAC).
- `top_protocols` – Top-N Protokolle nach Paketen **und** Bytes (`_ws.col.protocol`).
- `top_conversations` – Top-N IP-Conversations nach Paketen **und** Bytes.
- `tcp_health` – `tcp_packets`, `retransmissions` (+ `retransmission_pct`),
  `dup_acks` (+ `dup_ack_pct`), `resets`.
- `warnings` – `[{level, code, message}]`, z. B. `high_broadcast`,
  `high_multicast`, `high_tcp_retransmission`, `many_tcp_resets`.
- `sampled` / `packets_analyzed` / `packet_count` – bei großen Captures werden
  nur die ersten `NETPROBE_ANALYSIS_MAX_PACKETS` Pakete (Standard 200 000)
  ausgewertet; `packet_count` (via `capinfos`) weist die Gesamtzahl aus.

Schwellwerte (zentral in `app/core/config.py`, per Env überschreibbar):
`NETPROBE_ANALYSIS_BROADCAST_WARN_PCT` (10), `…_MULTICAST_WARN_PCT` (30),
`…_TCP_RETRANSMISSION_WARN_PCT` (5), `NETPROBE_ANALYSIS_TCP_RESET_WARN` (50).

**UI:** Die Capture-Liste (*Packet Captures*) verlinkt pro fertiger Aufnahme auf
`/captures/<id>` – eine eigene **Analyse-Seite** mit Tabs:

- **Pakete** – Tabelle (Zeit, Quelle, Ziel, Protokoll, Länge, Info) mit
  Display-Filter-Eingabe und Seiten-Navigation.
- **Conversations** – IP-Conversation-Tabelle.
- **Statistiken** – Diagramme Pakete/s und Bit/s über die Aufnahmedauer.
- **Übersicht** – Frame-Verteilung (Unicast/Broadcast/Multicast als Kacheln,
  Rahmen farbig bei Schwellwertüberschreitung), TCP-Gesundheit mit Prozentwerten,
  Top-Protokolle und Top-Conversations (Pakete + Bytes), Protokollhierarchie,
  DNS/TLS-SNI, Expert Infos. Warnungen erscheinen als farbige Banner oben.

### 2. Langzeit-Captures mit Rotation

Ein **CaptureJob** startet `dumpcap` als dauerhaften Hintergrund-Subprozess mit
Ringpuffer (`-b filesize` / `-b files`). Die rotierten PCAPNG-Dateien liegen unter
`/var/lib/netprobe/captures/<job_id>/`. `dumpcap` erhält `CAP_NET_RAW` über den
Dienst (`AmbientCapabilities`), **nicht** als root. Laufende Jobs werden beim
Dienst-Neustart automatisch wieder aufgenommen.

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `GET /api/capture-jobs` | viewer | Alle Jobs |
| `POST /api/capture-jobs` | admin | Job anlegen **und starten** (`iface`, `bpf`, `max_file_size_mb`, `max_files`) |
| `POST /api/capture-jobs/<id>/start` | admin | (Neu-)Start |
| `POST /api/capture-jobs/<id>/stop` | admin | Stoppen (SIGTERM an dumpcap) |
| `DELETE /api/capture-jobs/<id>` | admin | Stoppen, Job **und Verzeichnis** löschen |
| `GET /api/capture-jobs/<id>/captures` | viewer | Rotierte Dateien (Name, Größe, mtime) |
| `GET /api/capture-jobs/<id>/captures/<name>/download` | viewer | Einzelne PCAPNG herunterladen (Pfad wird gegen das Job-Verzeichnis validiert) |

Grenzen (via `/etc/netprobe/netprobe.env`): `NETPROBE_CAPTURE_JOB_MAX_FILESIZE_MB`
(Standard 1000), `NETPROBE_CAPTURE_JOB_MAX_FILES` (200),
`NETPROBE_CAPTURE_JOB_MAX_TOTAL_MB` (51200). Überschreitet `Größe × Anzahl` das
Gesamtlimit, wird die Dateianzahl automatisch reduziert.

**UI:** Seite *Langzeit-Captures* – Jobliste mit Status, Start/Stop/Löschen und
aufklappbarer Detailzeile, die die rotierten Dateien samt Download zeigt.

### 3. iperf3 (Client & Server)

Der Installer installiert `iperf3`. Der **Server** läuft als vom netprobe-Dienst
verwalteter Subprozess (kein Root, kein systemd nötig); der gewünschte Zustand
(`enabled`, `port`) wird im KV-Store persistiert und beim Dienst-Neustart
wiederhergestellt.

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `POST /api/iperf3/client` | admin | Client-Test (`host`, `port`, `duration_s` 1–60, `parallel` 1–32, `udp`, `reverse`), Ergebnis wird persistiert |
| `GET /api/iperf3/results` | viewer | Verlauf der Client-Tests |
| `GET /api/iperf3/server/status` | viewer | `{running, port}` des verwalteten Servers |
| `POST /api/iperf3/server` | admin | Server ein/aus + Port (`{enabled, port}`) |

Der Zielhost wird validiert (kein führendes `-`, nur Host-/IP-Zeichen).
Parsing über `iperf3 -J`: TCP → Senden/Empfangen (Mbit/s) + Retransmits,
UDP → Durchsatz + Jitter + Verlust.

**Optionaler Dauerbetrieb-Server:** `deploy/systemd/netprobe-iperf3.service` wird
installiert, aber **nicht** aktiviert. Bei Bedarf:
`sudo systemctl enable --now netprobe-iperf3` (dann den UI-Toggle **nicht**
zusätzlich verwenden – Portkonflikt).

**UI:** Seite *iperf3* – Server-Toggle mit Port, Client-Formular
(TCP/UDP, Dauer, Parallelität, Richtung) und Ergebnistabelle.

### 4. MTU-Test (Pfad-MTU)

`discover_path_mtu(target, min_mtu, max_mtu)` ermittelt die IPv4-Pfad-MTU per
`ping -4 -M do -s <mtu-28> -c 1 -- <target>` und binärer Suche. Zuerst wird die
untere Grenze geprüft (Erreichbarkeit), dann die obere; passt die obere nicht,
wird das größte funktionierende MTU zwischen beiden gesucht.

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `POST /api/mtu-test` | admin | `{target, min_mtu, max_mtu}` → `{ok, path_mtu, probes:[{mtu, ok}], …}`, wird persistiert |
| `GET /api/mtu-test/results` | viewer | Verlauf |

Harte Grenzen: `NETPROBE_MTU_MIN` (68), `NETPROBE_MTU_MAX` (9216). `ping` läuft
ohne Root (`CAP_NET_RAW` über den Dienst). Filtert ein Router ICMP mit DF, kann
das Ergebnis zu niedrig sein – der Probenverlauf macht das sichtbar.

**UI:** Seite *MTU-Test* – Formular (Ziel, Min/Max) und Ergebnis mit
Probentabelle.

### 5. Reports (Kunde/Kollegen)

Ein Report fasst **passive** (PCAP-)Analyse **und** die Ergebnisse **aller
aktiven Tests** zu einem druckfreundlichen **HTML**-Dokument zusammen; ist
**WeasyPrint** installiert, wird zusätzlich ein **PDF** erzeugt. Der komplette
Kennzahlen-Snapshot wird als JSON am Report gespeichert (`reports.data_json`),
sodass `GET /api/reports/<id>` ohne erneuten tshark-Lauf auskommt.

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `POST /api/reports` | admin | Report erstellen. Body: `capture_id` **oder** `job_id`, optional `customer`, `location`, `notes`, `sections`. |
| `POST /api/reports/preview` | admin | Nur `key_metrics` + `interpretations` (keine Datei/DB) – für die Vorschau. |
| `GET /api/reports` | viewer | Liste inkl. `key_metrics` + `interpretations` + Download-Links |
| `GET /api/reports/<id>` | viewer | Metadaten + Kennzahlen eines Reports |
| `GET /api/reports/<id>/download?fmt=html\|pdf` | viewer | HTML inline, PDF als Download |
| `DELETE /api/reports/<id>` | admin | Report + Dateien löschen |

**`sections`** (Checkboxen im UI): `summary`, `ping`, `speedtest`, `iperf3`,
`mtu`, `passive`. `summary` zieht die passive Analyse immer mit ein (für den
Broadcast-Anteil).

**Enthaltene Metriken:**

| Abschnitt | Inhalt |
|-----------|--------|
| **Zusammenfassung (Key Metrics)** | 5 Kacheln: Paketverlust Ping, UDP-Verlust (iperf3), TCP-Retransmissions (iperf3), Broadcast-Anteil, Speedtest-Download – jeweils mit Ampel (ok/warn/na) – plus Interpretationstexte („Paketverlust im Ping-Test (Gateway): 0,3 % – unauffällig"). |
| **Ping & WAN-Qualität** | je Dauerping-Job (letzte 24 h): Samples, Verlust %, RTT min/ø/max, Jitter. WAN-Ziele: aktuellster RTT ø / Jitter / Verlust. |
| **Speedtest** | aktuellster Lauf: Download/Upload (Mbit/s), Latenz, Jitter, Verlust. |
| **iperf3** | **TCP:** Durchsatz senden/empfangen, Retransmissions. **UDP:** Durchsatz, Jitter, Verlust %, verlorene/gesamte Pakete, Out-of-Order. |
| **MTU-Test** | letzte Tests: gefundene Pfad-MTU, Ziel, Suchbereich, Probenzahl. |
| **Passive Analyse (PCAP)** | Frame-Verteilung (Uni/Broad/Multicast), Top-Protokolle, Top-IP-Conversations (Pakete + Bytes), TCP-Gesundheit (Retransmissions/Dup-ACK/RST), Expert Infos. |

**Interpretationen/Warnungen** (Schwellwerte zentral in `app/core/config.py`,
per `NETPROBE_REPORT_*` überschreibbar):

| Bedingung | Standardschwelle | Env |
|-----------|------------------|-----|
| Ping-Paketverlust erhöht | > 1 % | `NETPROBE_REPORT_PING_LOSS_WARN_PCT` |
| iperf3-UDP-Verlust erhöht | > 1 % | `NETPROBE_REPORT_UDP_LOSS_WARN_PCT` |
| iperf3-TCP-Retransmissions erhöht | > 100 | `NETPROBE_REPORT_TCP_RETRANSMIT_WARN` |
| Jitter erhöht (Ping/Speedtest/UDP) | > 30 ms | `NETPROBE_REPORT_JITTER_WARN_MS` |
| Pfad-MTU < 1500 | 1500 | `NETPROBE_REPORT_MTU_INFO_BELOW` |
| Broadcast-/Multicast-Anteil hoch | 10 % / 30 % | `NETPROBE_ANALYSIS_BROADCAST_WARN_PCT` … |

Zeitfenster für übernommene Testergebnisse: `NETPROBE_REPORT_TEST_LOOKBACK_HOURS`
(Standard 168 h).

**Beispiel-Reportaufbau:**

```
netprobe-Report – <Kunde>
  Quelle: Capture #12 · Interface eth0 · 84,2 MB · Filter: –
  ── Zusammenfassung (Key Metrics) ─────────────
     [Paketverlust Ping 0,3 %]  [UDP-Verlust 0,8 %]  [TCP-Retransmissions 12]
     [Broadcast-Anteil 4,1 %]   [Speedtest-Download 94,2 Mbit/s]
     Interpretation:
       ✓ Paketverlust im Ping-Test (Gateway): 0,3 % – unauffällig.
       ✓ iperf3-UDP-Paketverlust (10.0.0.2): 0,8 % – unauffällig.
       ℹ iperf3-TCP: 940,0 Mbit/s Durchsatz, 12 Retransmissions.
       ℹ Pfad-MTU zu 1.1.1.1: 1500 Byte.
  ── Ping & WAN-Qualität ───────────────────────  (Tabellen)
  ── Speedtest ────────────────────────────────  (Tabelle)
  ── iperf3 (TCP/UDP) ─────────────────────────  (TCP-Tabelle + UDP-Tabelle)
  ── MTU-Test ─────────────────────────────────  (Tabelle)
  ── Passive Analyse (PCAP) ───────────────────  (Frame-Verteilung, Protokolle,
                                                 Conversations, TCP-Gesundheit, Expert)
  ── Notizen ──────────────────────────────────
```

- Speicherort: **`/var/lib/netprobe/reports/<id>.html`** (und `.pdf`).
- WeasyPrint wird vom Installer als Extra `pdf` ins venv installiert
  (`libpango-1.0-0`, `libpangoft2-1.0-0`, `fonts-dejavu-core` als Systempakete).
  Fehlt es, bleibt die Report-Funktion nutzbar – nur HTML; das `error`-Feld weist
  darauf hin, das HTML kann im Browser gedruckt werden.
- Die passive Analyse nutzt die Sampling-Grenze `NETPROBE_ANALYSIS_MAX_PACKETS`,
  damit auch große Captures schnell bleiben.

**UI:** Button *Report erstellen* auf `/captures/<id>` öffnet ein Formular
(Kunde, Standort, Notizen, Abschnitts-Checkboxen, **Vorschau**-Button für die Key
Metrics). Nach der Erzeugung erscheinen die Key-Metric-Kacheln plus Links für
HTML und – falls vorhanden – PDF.

### 6. VoIP-/3CX-Verbindungstest

Prüft die Erreichbarkeit einer 3CX-/SIP-Anlage **ohne einen echten Anruf**. Je
konfiguriertem Server werden folgende Teilprüfungen ausgeführt und als ein
`voip_tests`-Datensatz aggregiert:

| Teilprüfung | Verfahren |
|-------------|-----------|
| SIP UDP / TCP | SIP-`OPTIONS`-Ping am `sip_port`, Statuszeile wird ausgewertet (auch 401/403/405 = „SIP antwortet") |
| SIP-Antwortzeit / -Code | aus dem `OPTIONS`-Lauf über den konfigurierten Transport |
| SIP-over-TLS | TLS-Handshake am `sip_tls_port` (0 = aus): Handshake-Zeit, Zertifikatsablauf, `tls_verified` |
| WebRTC / HTTPS | TCP-Connect je Port aus `webrtc_ports` (Standard `443,5001`) |
| ICMP-Qualität | 10 Pings zum Host → Verlust % + RTT ø (best effort) |
| Registrierung (optional) | ein einzelner `REGISTER` mit Digest-Auth – nur wenn SIP-Zugangsdaten im Request mitgegeben werden |

**Gesamturteil `ok`:** SIP über irgendeinen Transport erreichbar **und** – falls
ein TLS-Port gesetzt ist – TLS-Handshake erfolgreich (**und** ggf. Registrierung
erfolgreich). WebRTC-/ICMP-Auffälligkeiten stehen im `error`-Feld, sind aber kein
hartes Kriterium.

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `GET /api/voip/servers` | viewer | Liste + jeweils letzter Test (Status-Ampel) |
| `POST /api/voip/servers` | admin | 3CX-Server anlegen |
| `PATCH /api/voip/servers/<id>` | admin | ändern (Zeitplan sofort neu synchronisiert) |
| `DELETE /api/voip/servers/<id>` | admin | löschen (Historie via CASCADE) |
| `POST /api/voip/servers/<id>/test` | admin | Ad-hoc-Lauf. Body optional `{username, password}` für den Registrierungstest |
| `GET /api/voip/servers/<id>/history` | viewer | `range=24h\|7d\|30d\|all` |
| `GET /api/voip/history` | viewer | letzte Läufe über alle Server (Dashboard) |

Der Scheduler misst jeden aktivierten Server im Intervall `interval_s` (min. 60 s).
Retention: `NETPROBE_VOIP_RETENTION_DAYS` (Standard 365). Keine externen Tools –
reine Standardbibliothek (`socket`/`ssl`).

**SIP-Zugangsdaten** für den Registrierungstest werden **nie gespeichert oder
geloggt** und über TLS nur über einen **verifizierten** Kanal gesendet (siehe
`docs/security.md`).

**CLI:** `python -m app.cli voip-test --server pbx.kunde.example --port 5061`
(nicht persistierter Einmal-Test).

**UI:** Seite *VoIP / 3CX* – Server-Tabelle mit Ampel, „Jetzt testen", Verlaufs-
Diagramme (SIP-/TLS-Antwortzeit, Verlust) und Lauf-Historie. Dashboard-Karte
„VoIP / 3CX", sobald mindestens ein Server konfiguriert ist.

### 7. Traceroute / Pfad-Analyse

Ermittelt den Netzpfad zu einem Ziel inkl. **per-Hop** Loss/Latenz, optionaler
**Pfad-MTU** zum Ziel und **Geo/ASN** je Hop. Zwei Läufe lassen sich
gegenüberstellen (z. B. vor/nach einer Routing-Änderung).

**Engine:** bevorzugt `mtr --json` (Paket `mtr-tiny`), Fallback `traceroute`.
Beide laufen mit dem `CAP_NET_RAW` des Dienstes ohne Root. Modi: `icmp`
(Standard), `udp`, `tcp` (mit Ziel-Port).

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `GET/POST /api/traceroute/targets` | viewer/admin | gespeicherte Ziele (wie Dauerping) |
| `PATCH/DELETE /api/traceroute/targets/<id>` | admin | ändern/löschen (Zeitplan sofort synchronisiert) |
| `POST /api/traceroute/run` | admin | Ad-hoc: `{host, mode, tcp_port, queries, max_hops, resolve_geo, measure_mtu}` |
| `POST /api/traceroute/targets/<id>/run` | admin | Lauf für ein gespeichertes Ziel |
| `GET /api/traceroute/history` | viewer | Verlauf (`range`, `limit`, `target_id`) |
| `GET /api/traceroute/<id>` | viewer | Lauf inkl. Hops |
| `GET /api/traceroute/compare?a=<id>&b=<id>` | viewer | Diff zweier Läufe (Pfadänderung, Δ Latenz) |

Retention: `NETPROBE_TRACEROUTE_RETENTION_DAYS` (90). Obergrenze Hops:
`NETPROBE_TRACEROUTE_MAX_HOPS_LIMIT` (60). Läufe eines gelöschten Ziels bleiben
erhalten (lose Referenz, kein Fremdschlüssel).

**Geolocation (Hybrid).** Je Hop-IP:

1. Cache (`geoip_cache`, TTL `NETPROBE_GEOIP_CACHE_DAYS`, Standard 30 Tage).
2. Lokale MaxMind-DB `GeoLite2-City.mmdb` / `GeoLite2-ASN.mmdb` unter
   `/var/lib/netprobe/geoip/`, falls vorhanden (`python -m maxminddb`, offline).
3. Sonst **ip-api.com** Batch-Endpoint über **HTTP** (`NETPROBE_GEOIP_ENDPOINT`),
   5 s Timeout, Rate-Limit `NETPROBE_GEOIP_ONLINE_RATE_PER_MIN` (40/min, Free-Limit
   ist 45).

Private/Bogon-IPs (RFC1918, CGNAT 100.64/10, Loopback, Link-Local, Multicast)
werden **nie** an ip-api.com gesendet. Abschalten: `NETPROBE_GEOIP_ENABLED=false`
(gar keine Auflösung) bzw. `NETPROBE_GEOIP_ONLINE_ENABLED=false` (nur lokale DB).
Siehe `docs/security.md` – dies ist die einzige Ausnahme vom „keine Cloud"-Prinzip.

**MaxMind-DBs beziehen:** kostenloses Konto bei MaxMind, Lizenzschlüssel als
`NETPROBE_MAXMIND_LICENSE_KEY` beim `install.sh`-Lauf setzen (lädt beide DBs),
oder die `.mmdb`-Dateien manuell nach `/var/lib/netprobe/geoip/` legen
(`chown netprobe:netprobe`). Ein späteres Auffrischen genügt – der Reader erkennt
die neue Datei an der mtime.

**CLI:** `python -m app.cli traceroute --target 8.8.8.8 --geo [--mtu] [--mode tcp]`.

**UI:** Seite *Traceroute* – Sofortlauf-Formular, gespeicherte Ziele, Hop-Tabelle
(IP/Hostname/Land/Stadt/ASN/ISP/Latenz/Loss/MTU), **Weltkarte** (Leaflet +
OpenStreetMap-Kacheln), Vergleichsansicht und Verlauf.

## Deinstallation

```bash
sudo systemctl disable --now netprobe netprobe-iperf3
sudo rm -f /etc/systemd/system/netprobe.service /etc/systemd/system/netprobe-iperf3.service
sudo systemctl daemon-reload

sudo rm -f /etc/nginx/sites-enabled/netprobe.conf /etc/nginx/sites-available/netprobe.conf
sudo systemctl reload nginx

sudo rm -f /etc/logrotate.d/netprobe
sudo rm -rf /opt/netprobe

# Daten & Konfiguration – nur entfernen, wenn wirklich gewünscht (Backup vorher!):
# (enthält DB, Captures, Reports unter /var/lib/netprobe)
sudo rm -rf /var/lib/netprobe /var/log/netprobe /etc/netprobe

# Systembenutzer entfernen:
sudo userdel netprobe 2>/dev/null || true
```

Netplan-Änderungen (falls während der Installation vorgenommen) liegen in
`/etc/netplan/99-netprobe.yaml`; Sicherungskopien unter `/etc/netplan/backup_*`.
