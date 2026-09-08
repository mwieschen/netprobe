# netprobe

**netprobe** ist eine lokale Web-Appliance für **Netzwerk- und WAN-Monitoring**,
die im LAN über einen Browser administriert wird. Sie misst Internet-Durchsatz
(Ookla Speedtest), WAN-Qualität (Latenz/Jitter/Paketverlust), führt Dauerpings
durch und ermöglicht sichere Packet Captures mit Basisanalyse – alles lokal,
**ohne Cloud-Dienste und ohne Telemetrie**.

> Zielplattform: **Ubuntu Server 22.04 / 24.04**. Backend: FastAPI + SQLAlchemy.
> Frontend: React + TypeScript + Vite + ECharts. Zeitzone: intern UTC, Anzeige Europe/Berlin.

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

# Offizielle Ookla Speedtest CLI (NICHT das Python-Paket speedtest-cli)
curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash -
sudo apt-get install -y speedtest
```

> **Hinweis:** Ist eine Systemabhängigkeit nicht verfügbar, liefert netprobe eine
> verständliche Fehlermeldung (z. B. „Benötigtes Programm 'speedtest' wurde nicht
> gefunden“). Das Dashboard zeigt unter *Werkzeuge*, welche Programme vorhanden sind.

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
