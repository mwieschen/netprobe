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
- **VoIP / 3CX** – SIP-/SIP-TLS-/WebRTC-Erreichbarkeit, SIP-`OPTIONS`-Antwortzeit,
  TLS-Handshake & Zertifikatsablauf, ICMP-Verlust, optionaler Registrierungstest
  (Zugangsdaten werden nicht gespeichert). **Kein Anruf.**
- **Reports** – passive PCAP-Analyse + aktive Testergebnisse als druckfreundliches
  HTML (optional PDF via WeasyPrint).
- **Authentifizierung** – lokale Benutzer mit Argon2, RBAC (`admin`/`viewer`),
  Rate-Limiting, Audit-Log, HttpOnly/SameSite-Cookies.

Architektur- und Sicherheitsdetails: [`docs/architecture.md`](docs/architecture.md),
[`docs/security.md`](docs/security.md).

---

## Schnellinstallation (Copy & Paste)

Auf einem frischen Ubuntu-Server (als Benutzer mit sudo):

```bash
# 1. Repository holen
git clone https://example.invalid/netprobe.git
cd netprobe

# 2. Installer ausführen (installiert alle Abhängigkeiten, baut Frontend,
#    richtet systemd + nginx ein und legt den Admin an)
sudo ./install.sh
```

Der Installer fragt interaktiv ab:
1. **Netzwerk**: unverändert lassen / DHCP / statische IPv4 (nur bei 2/3 wird
   Netplan verändert – ausschließlich per `netplan try`, lokale Konsole empfohlen).
2. **Initialer Admin-Benutzer** und Passwort (Passwort wird nie im Klartext geloggt).

Nach Abschluss:

```
Weboberfläche:     http://<server-ip>/
API-Dokumentation: http://<server-ip>/api/docs
```

Der Installer ist **idempotent** – ein erneuter Aufruf aktualisiert die Anwendung,
ohne Datenbank oder Konfiguration zu zerstören.

### Ubuntu-26.04-Besonderheiten

Der Installer erkennt **22.04, 24.04 und 26.04** automatisch. Für 26.04 gilt:

- **Python 3.14**: Das Backend läuft mit 3.12–3.14. Alle Abhängigkeiten bringen
  vorgebaute Wheels für 3.14 mit; falls nicht, baut pip aus dem Quellcode
  (`build-essential` + `python3-dev` werden installiert). Der systemd-Dienst startet
  aus einem venv und ist damit unabhängig von der konkreten Python-Version.
- **Node.js 24**: wird bevorzugt von NodeSource installiert; schlägt das auf einem noch
  nicht unterstützten Codename fehl, greift automatisch das Ubuntu-eigene Node-Paket
  (≥ 20 genügt für den Vite-7-Build).
- **Ookla Speedtest CLI**: Das packagecloud-Repo führt 26.04 evtl. noch nicht. Der
  Installer nutzt daher den **24.04-Fallback-Codename (`noble`)** und – falls auch das
  fehlschlägt – lädt automatisch das **offizielle Ookla-Tarball** und legt die Binary
  nach `/usr/local/bin/speedtest`. Siehe unten „Speedtest CLI manuell".

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
# Auf 26.04 den 24.04-Codename erzwingen, solange 26.04 nicht geführt wird:
curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh \
    | sudo os=ubuntu dist=noble bash -
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
| `speedtest`-Paket nicht gefunden | Ookla-Repo führt 26.04 noch nicht | Installer nutzt automatisch `noble`-Fallback + Tarball; manuell siehe „Speedtest CLI manuell" |
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
  scripts/          backup.sh, restore.sh
  docs/             architecture.md, security.md, operations.md
  install.sh        Installer für Ubuntu 22.04/24.04
  .env.example      Konfigurationsvorlage
```

---

## Lizenz

MIT – siehe [`LICENSE`](LICENSE).
