# netprobe

**netprobe** ist eine lokale Web-Appliance für **Netzwerk- und WAN-Monitoring**,
die im LAN über einen Browser administriert wird. Sie misst Internet-Durchsatz
(Ookla Speedtest), WAN-Qualität (Latenz/Jitter/Paketverlust), führt Dauerpings
durch und ermöglicht sichere Packet Captures mit Basisanalyse – alles lokal,
**ohne Cloud-Dienste und ohne Telemetrie**.

> Zielplattform: **Ubuntu Server 22.04 / 24.04 / 26.04**. Backend: FastAPI + SQLAlchemy
> (Python 3.12–3.14). Frontend: React 19 + TypeScript + Vite 7 + ECharts, gebaut mit
> Node.js ≥ 20 (LTS 24 empfohlen). Zeitzone: intern UTC, Anzeige Europe/Berlin.

---

## Funktionsumfang

- **Network Diagnostics** – zentrale Seite: Ping, aktive TCP-Retransmissions, MTU,
  Broadcast-Analyse, Traceroute und Speedtest gebündelt zu bis zu 10 frei
  wählbaren Zielen, mit Ampel-Status (OK/WARN/KRITISCH) je Ziel und gesamt,
  Historie und speicherbaren Presets. Reine Orchestrierung der untenstehenden
  Einzel-Features. Details: [`docs/diagnostics.md`](docs/diagnostics.md).
- **Speedtest** – offizielle Ookla Speedtest CLI, manuell & geplant, feste Serverwahl
  oder Auto, Diagramme für 24 h / 7 d / 30 d / benutzerdefiniert, Job-Lock gegen
  parallele Tests.
- **WAN-Qualität** – konfigurierbare Ziele (fping/ping), Paketverlust, min/avg/max RTT,
  Standardabweichung und Jitter, Zeitreihen für Latenz/Jitter/Loss, Down-Events.
- **Dauerping** – mehrere parallele Ziele, Intervall ≥ 1 s, Start/Stop/Pause/Löschen,
  **Live-Updates via Server-Sent Events**, Rohdaten 14 Tage, 5-Minuten-Aggregate langfristig.
- **Packet Capture** – dumpcap für Aufnahme, tshark für Analyse (Protokollhierarchie,
  Top-Endpunkte, TCP/UDP-Conversations, DNS-Queries, TCP-Retransmissions/Resets, TLS-SNI,
  Expert Infos), PCAPNG-Download, CSV/JSON-Export, sicherer Display-Filter (Limit 1.000 Pakete).
- **Langzeit-Captures** – dumpcap-Ringpuffer-Jobs mit Auto-Resume nach Neustart.
- **iperf3** – verwalteter Server + Client-Tests (TCP/UDP), Durchsatz/Jitter/Verlust.
- **MTU-Test** – Pfad-MTU-Ermittlung per binärer Suche (`ping -M do`).
- **Traceroute** – Pfad-Analyse via `mtr`/`traceroute` mit per-Hop Loss/Latenz,
  optionaler Pfad-MTU, Geo/ASN je Hop (lokale MaxMind-DB oder ip-api.com),
  Weltkarte (Leaflet/OpenStreetMap) und Vergleich zweier Läufe.
- **System-Health-Checks** – konfigurierbare Probes für DNS, HTTP(S) (inkl.
  Zertifikatslaufzeit), SMTP/IMAP (STARTTLS-Handshake, kein Login) und NTP
  (Zeitabweichung/Stratum), periodisch mit Historie und Dashboard-Übersicht.
- **Dashboard** – Gesundheitsstatus, aktuelle Messwerte, Netzwerk-Topologie
  (Status-Ampeln aller Ziele) und Störungs-Timeline der letzten 24 h.
- **VoIP / 3CX** – SIP-/SIP-TLS-/WebRTC-Erreichbarkeit, SIP-`OPTIONS`-Antwortzeit +
  Server-Header/Methoden, volle TLS-Details (Version/Cipher/Zertifikat), ICMP-Verlust,
  optionaler Registrierungstest (Zugangsdaten werden nicht gespeichert). **Kein Anruf.**
  Optional paralleler PCAP-Mitschnitt mit SIP-Dialog-, SDP-/Codec- und RTP-Analyse
  (Jitter/Verlust); separate Analyse vorhandener Captures.
- **TCP-Retransmission-Monitoring** – passive Kurz-Aufnahme (dumpcap/tshark) oder
  aktiver Test mit gezielten TCP-Connect-Proben zu frei wählbaren Zielen
  (inkl. speicherbarer Ziel-Presets), ordnet betroffene Ziele LAN/WAN zu und
  klassifiziert die wahrscheinliche Ursache automatisch (lokales Netzwerk,
  Provider/WAN-Anbindung, einzelner Server, Up-/Downlink-Überlastung). Historie,
  Korrelation mit anderen Tests, Report-Export für Provider-Nachweis. Details:
  [`docs/retransmissions.md`](docs/retransmissions.md).
- **Reports** – passive PCAP-Analyse + aktive Testergebnisse als druckfreundliches
  HTML (optional PDF via WeasyPrint).
- **Scheduler** – zentrale Übersicht aller automatisch geplanten Tests
  (Speedtest, Retransmission-Monitoring, jedes WAN-/VoIP-/Traceroute-Ziel, jede
  Health-Probe) mit globalem Pause/Aktivieren-Schalter (sofort wirksam, kein
  Neustart nötig). Details: [`docs/scheduler.md`](docs/scheduler.md).
- **Authentifizierung** – lokale Benutzer mit Argon2, RBAC (`admin`/`viewer`),
  Rate-Limiting, Audit-Log, HttpOnly/SameSite-Cookies.

Architektur- und Sicherheitsdetails: [`docs/architecture.md`](docs/architecture.md),
[`docs/security.md`](docs/security.md).

---

## Schnellinstallation (ein Befehl)

```bash
curl -sSL https://raw.githubusercontent.com/mwieschen/netprobe/main/install.sh | sudo bash
```

Derselbe Befehl installiert **und** aktualisiert – der Installer erkennt selbst, was zu
tun ist, und stellt keine Rückfragen:

- **Keine Installation vorhanden** → Neuinstallation (Pakete, Benutzer, venv,
  Konfiguration, Datenbank, Admin, systemd, nginx). Das initiale Admin-Passwort wird
  zufällig erzeugt und **einmalig** angezeigt (oder per `NETPROBE_ADMIN_PASSWORD` vorgeben).
- **Installation vorhanden** → Smart-Update: Backend-Code, Frontend, Python-Abhängigkeiten,
  systemd-Unit und Datenbank-Migrationen werden verglichen, **nur Geändertes** wird
  eingespielt. Vorher entsteht immer ein Snapshot (Code + Konfiguration + DB); schlägt die
  Migration oder der Healthcheck fehl, wird **automatisch zurückgerollt**. Ist alles
  aktuell, wird nichts neu gestartet – fehlende Teile werden trotzdem repariert.

Nach der Installation liegt der Installer zusätzlich unter `/opt/netprobe/install.sh`:

```bash
sudo bash /opt/netprobe/install.sh                 # Update auf den neuesten Stand
sudo bash /opt/netprobe/install.sh --rollback      # zurück auf den letzten Snapshot
sudo bash /opt/netprobe/install.sh --backup-only   # nur Snapshot anlegen
sudo bash /opt/netprobe/install.sh --help          # alle Optionen
```

Ohne Internet-Zugriff auf GitHub: Release-Paket auf dem Entwicklungsrechner bauen
(`bash scripts/build-release.sh`), übertragen, entpacken und `sudo bash install.sh`
im entpackten Verzeichnis ausführen – gleiches Verhalten.

Ausführlich: [`docs/installation.md`](docs/installation.md) (Installation, Optionen,
Umgebungsvariablen) und [`docs/update.md`](docs/update.md) (Update, Snapshots, Rollback).

> **Hinweis:** `mwieschen/netprobe` ist ein Platzhalter – im Befehl oben **und** als
> Default `NETPROBE_REPO` in `install.sh` durch das echte GitHub-Repository ersetzen.

Nach Abschluss:

```
Weboberfläche:     http://<server-ip>/
API-Dokumentation: http://<server-ip>/api/docs
```

### Ubuntu-26.04-Besonderheiten

Der Installer erkennt **22.04, 24.04 und 26.04** automatisch. Für 26.04 gilt:

- **Python 3.14**: Das Backend läuft mit 3.12–3.14. Alle Abhängigkeiten bringen
  vorgebaute Wheels für 3.14 mit; falls nicht, baut pip aus dem Quellcode
  (`build-essential` + `python3-dev` werden installiert). Der systemd-Dienst startet
  aus einem venv und ist damit unabhängig von der konkreten Python-Version.
- **Node.js 24**: wird bevorzugt von NodeSource installiert; schlägt das auf einem noch
  nicht unterstützten Codename fehl, greift automatisch das Ubuntu-eigene Node-Paket
  (≥ 20 genügt für den Vite-7-Build).
- **Ookla Speedtest CLI**: Das packagecloud-Repo führt derzeit weder `noble` (24.04)
  noch 26.04. Der Installer nutzt daher auf 24.04 **und** 26.04 den
  **22.04-Fallback-Codename (`jammy`)** und – falls auch das fehlschlägt – lädt
  automatisch das **offizielle Ookla-Tarball** und legt die Binary nach
  `/usr/local/bin/speedtest`. Eine von einem älteren/fehlgeschlagenen Lauf
  hinterlassene ungültige Paketquelle (z. B. mit `noble`) wird beim nächsten Aufruf
  automatisch entfernt, bevor `apt-get update` läuft – betroffene Installationen
  heilen sich durch erneutes Ausführen von `install.sh` selbst. Siehe unten
  „Speedtest CLI manuell".

---

## Manuelle Installation der Systemabhängigkeiten

Falls Pakete einzeln benötigt werden:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev build-essential \
    nginx sqlite3 tshark wireshark-common fping iputils-ping mtr-tiny \
    curl jq ca-certificates

# Node.js LTS
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo bash -
sudo apt-get install -y nodejs

# Node.js 24 (LTS)
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo bash -
sudo apt-get install -y nodejs

# Offizielle Ookla Speedtest CLI (NICHT das Python-Paket speedtest-cli)
# Auf 24.04/26.04 den 22.04-Codename erzwingen, solange deren Repo dort nicht führt:
curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh \
    | sudo os=ubuntu dist=jammy bash -
sudo apt-get install -y speedtest
```

### Speedtest CLI manuell (Tarball-Fallback)

Falls das APT-Repository die Speedtest CLI nicht bereitstellt (typisch auf sehr neuen
Ubuntu-Versionen), das **offizielle Tarball** von Ookla installieren:

```bash
# Architektur ggf. anpassen: x86_64 | aarch64 | armhf | i386
curl -fsSL https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-x86_64.tgz -o /tmp/st.tgz
tar -xzf /tmp/st.tgz -C /tmp
sudo install -m 0755 /tmp/speedtest /usr/local/bin/speedtest
speedtest --accept-license --accept-gdpr --version
```

> **Hinweis:** Ist eine Systemabhängigkeit nicht verfügbar, liefert netprobe eine
> verständliche Fehlermeldung (z. B. „Benötigtes Programm 'speedtest' wurde nicht
> gefunden“). Das Dashboard zeigt unter *Werkzeuge*, welche Programme vorhanden sind.

---

## Bekannte Issues & Workarounds (Ubuntu 26.04)

| Problem | Ursache | Workaround |
|---------|---------|------------|
| `speedtest`-Paket nicht gefunden | Ookla-Repo führt weder `noble` (24.04) noch 26.04 | Installer nutzt automatisch `jammy`-Fallback + Tarball; manuell siehe „Speedtest CLI manuell" |
| `apt-get update` schlägt mit „… does not have a Release file" fehl (bricht `install.sh` komplett ab) | Ungültige Ookla-Paketquelle (z. B. `noble`) von einem älteren Lauf vor diesem Fix hinterlassen | `sudo ./install.sh` erneut ausführen – entfernt die fehlerhafte Quelle automatisch vor `apt-get update`; manuell: `sudo rm -f /etc/apt/sources.list.d/*ookla*.list` |
| Packet Capture: `Permission denied` beim Schreiben der `.pcap` | Capture-Ordner nicht `netprobe:wireshark 2770` | `sudo ./scripts/fix-permissions.sh` (setzt Owner/Modus, setgid) |
| Dauerping / WAN-Test liefert nur Timeouts, Log: `Operation not permitted` | Dienst ohne `CAP_NET_RAW` (ältere systemd-Unit ohne `AmbientCapabilities`) | Neue Unit einspielen: `sudo ./install.sh` **oder** `sudo ./scripts/fix-permissions.sh` |
| Speedtest im UI schlägt fehl, CLI manuell ok | `HOME` des Dienstes (`/opt/netprobe`) durch `ProtectSystem=strict` read-only → Ookla-CLI kann Lizenz nicht speichern | Neue Unit setzt `HOME=/var/lib/netprobe`; `sudo ./scripts/fix-permissions.sh` bestätigt die Lizenz vorab |
| NodeSource-Setup schlägt fehl | Codename in NodeSource unbekannt | Installer fällt auf Ubuntu-Node-Paket zurück; alternativ `sudo apt-get install -y nodejs npm` |
| `pip`/Wheel-Build für Python 3.14 langsam | Noch keine vorgebauten Wheels | `build-essential` + `python3-dev` sind installiert; Build aus Quellcode läuft automatisch |
| `pip install ../README.md`-Fehler bei eigenem Build | Neuere setuptools verbieten Pfade außerhalb des Pakets | Bereits behoben – `backend/pyproject.toml` referenziert keine externe README mehr |
| `dumpcap: Permission denied` | Capabilities/Gruppe fehlen | `sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/dumpcap` und `netprobe` in Gruppe `wireshark` |

---

## Entwicklung (lokal)

### Backend

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Datenbank vorbereiten
export NETPROBE_DATABASE_URL="sqlite:///./dev.sqlite"
export NETPROBE_SECRET_KEY="dev-secret"
alembic upgrade head
python -m app.cli create-admin --username admin      # Passwort wird abgefragt

# API starten (Auto-Reload)
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # Dev-Server auf http://localhost:5173 (Proxy → 127.0.0.1:8000)
```

Produktions-Build: `npm run build` → `frontend/dist` (wird vom Backend statisch ausgeliefert).

---

## Tests, Linting, Typprüfung

```bash
cd backend
. .venv/bin/activate
pytest            # Unit-Tests (Parser/Berechnung) + API-Smoke-Tests
ruff check .      # Linting
mypy app          # statische Typprüfung
```

Frontend-Typprüfung: `cd frontend && npm run lint` (bzw. `npm run build`).

---

## Konfiguration

Alle Einstellungen laufen über Umgebungsvariablen bzw.
**`/etc/netprobe/netprobe.env`** (Vorlage: [`.env.example`](.env.example)).
Wichtige Werte: `NETPROBE_SECRET_KEY`, Pfade, Tool-Binaries, Speedtest-Intervall,
Capture- und Retention-Limits. Keine Secrets im Repository.

---

## Betrieb

Update, Backup, Restore, Fehleranalyse und Deinstallation:
siehe [`docs/operations.md`](docs/operations.md).

Kurzreferenz:

```bash
systemctl status netprobe          # Dienststatus
journalctl -u netprobe -f          # Logs
sudo bash /opt/netprobe/install.sh               # Update (nur Geändertes)
sudo bash /opt/netprobe/install.sh --rollback    # letzten Snapshot zurückspielen
sudo ./scripts/backup.sh           # Backup der SQLite-DB
sudo ./scripts/restore.sh <datei>  # Wiederherstellung
curl http://localhost/api/health   # Healthcheck
```

---

## Projektstruktur

```
netprobe/
  backend/          FastAPI-Backend (app/), Alembic-Migrationen, Tests, pyproject.toml
  frontend/         React + TypeScript + Vite (ECharts)
  deploy/           systemd-Unit, nginx-Site, logrotate
  scripts/          build-release.sh (Release-Paket), backup.sh, restore.sh, fix-permissions.sh
  docs/             installation.md, update.md, architecture.md, security.md, operations.md
  install.sh        selbstheilender Installer/Updater für Ubuntu 22.04/24.04/26.04
  .env.example      Konfigurationsvorlage
```

---

## Lizenz

MIT – siehe [`LICENSE`](LICENSE).
