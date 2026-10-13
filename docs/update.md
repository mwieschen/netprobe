# netprobe – Update, Snapshots und Rollback

## Update

Updates laufen mit demselben Befehl wie die Installation:

```bash
sudo bash /opt/netprobe/install.sh
# oder
curl -sSL https://raw.githubusercontent.com/youruser/netprobe/main/install.sh | sudo bash
```

Der Installer findet `/opt/netprobe/venv`, wechselt in den **Smart-Update**-Modus und
spielt nur ein, was sich geändert hat. Er ist beliebig oft ausführbar: ist alles aktuell,
wird nichts neu gestartet.

### Änderungserkennung

| Komponente | Erkennung | Aktion bei Änderung |
|---|---|---|
| Backend-Code | md5 über `backend/app`, `backend/alembic`, `alembic.ini`, `pyproject.toml`, Betriebsskripte – Quelle vs. `/opt/netprobe` | Verzeichnisse ersetzen, netprobe-Paket offline neu installieren (`pip --no-deps`) |
| Python-Abhängigkeiten | sha256 von `pyproject.toml` vs. `.install-state/deps.sha256`; venv defekt (z. B. nach Ubuntu-Upgrade) | `pip install` (venv ggf. neu) |
| Frontend (Release-Paket) | md5 des vorgebauten `dist` vs. installiertes `dist` | `dist` atomar austauschen |
| Frontend (Quellcode) | md5 der Frontend-Quellen vs. `.install-state/frontend-src.md5`; `dist/index.html` fehlt | bauen (Node.js wird bei Bedarf installiert, vorher Platzprüfung ~1 GB) |
| systemd-Unit | Vergleich mit `/etc/systemd/system/netprobe.service` | Unit ersetzen, `daemon-reload` |
| Migrationen | `alembic current` (DB) vs. `alembic heads` (neuer Code) | `alembic upgrade head`, danach erneute Prüfung |

Beispiel:

```
  Änderungsanalyse
    Installiert:          1.0.0 (5e59739, 2026-10-01)
    Neu:                  1.1.0 (8a1c2f0, 2026-10-05)
    Backend-Code:         geändert -> wird eingespielt
    Python-Abhängigkeiten: unverändert
    Frontend:             geändert -> vorgebautes dist
    systemd-Unit:         unverändert
    Datenbank-Migrationen: ausstehend (0013_network_diagnostics -> 0014_xyz)
  …
==============================================================
 netprobe wurde auf 1.1.0 (8a1c2f0, 2026-10-05) aktualisiert.
==============================================================
  Eingespielt:
    - Backend-Code
    - Frontend
    - Datenbank-Migrationen (jetzt: 0014_xyz)
  Snapshot:       /var/lib/netprobe/backups/snapshot_20261005_101500
  Rollback:       sudo bash /opt/netprobe/install.sh --rollback
```

### Ablauf

1. Vorbedingungen prüfen, Änderungsanalyse anzeigen
2. fehlende Systempakete nachinstallieren (sonst kein `apt-get update`)
3. **nichts geändert** → nur Selbstheilung (Verzeichnisse/Rechte, Konfiguration, nginx,
   logrotate, Dienst starten falls gestoppt, Admin falls keiner existiert), Smoke-Tests,
   Ende – **ohne** Neustart
4. sonst: **Snapshot** → Dienst stoppen → geänderte Komponenten einspielen →
   Migrationen + Prüfung → Unit/nginx/logrotate → Dienst starten → Smoke-Tests
5. Schlägt ab dem Snapshot irgendein Schritt fehl (Build, pip, Migration, Prüfung,
   Backend antwortet nach 30 s nicht), wird **automatisch auf den Snapshot
   zurückgerollt** und der vorherige Stand wieder gestartet.

Datenbank, `/etc/netprobe/netprobe.env`, Admin-Benutzer, Firewall und Netzwerk bleiben
unangetastet. Lokal angepasste nginx-Site oder logrotate-Datei werden **nicht**
überschrieben; die neue Vorlage liegt dann als `<datei>.netprobe-new` daneben.

### Manuell erzwingen

```bash
sudo bash /opt/netprobe/install.sh --rebuild-frontend   # Frontend neu bauen/einspielen
sudo bash /opt/netprobe/install.sh --run-migrations     # alembic upgrade head + Prüfung
sudo bash /opt/netprobe/install.sh --force              # alle Komponenten neu einspielen
```

Bestimmten Stand installieren: `NETPROBE_REF=v1.1.0` (Tag/Branch/Commit) bzw.
`NETPROBE_SOURCE_URL=<tar.gz>`; Variablen hinter `sudo` setzen:

```bash
sudo NETPROBE_REF=v1.1.0 bash /opt/netprobe/install.sh
```

Ohne GitHub-Zugriff: Release-Paket übertragen, entpacken, darin `sudo bash install.sh`.

## Snapshots

Jedes Update legt vorher einen Snapshot unter `/var/lib/netprobe/backups/snapshot_<zeit>/` an:

| Datei | Inhalt |
|---|---|
| `db.sqlite.gz` | konsistente DB-Kopie (`sqlite3 .backup`, WAL-sicher; auch mit `scripts/restore.sh` nutzbar) |
| `app.tar.gz` | `/opt/netprobe` ohne venv (Code, Frontend, Skripte, Installer-Zustand) |
| `config.tar.gz` | `/etc/netprobe`, systemd-Units, nginx-Site, logrotate |
| `MANIFEST` | Version, Zeitpunkt, Anlass (`update`, `install`, `backup`, `rollback`) |

Vor dem Snapshot wird der Platz geprüft (DB-Größe + Anwendung + 100 MB). Aufbewahrt
werden die neuesten 5 (`NETPROBE_KEEP_SNAPSHOTS`). Snapshots enthalten den `SECRET_KEY`
und sind nur für root/`netprobe` lesbar.

```bash
sudo bash /opt/netprobe/install.sh --backup-only    # Snapshot ohne Update
sudo bash /opt/netprobe/install.sh --list-backups
```

Die täglichen DB-Backups per `scripts/backup.sh` (cron) sind davon unabhängig, siehe
[`operations.md`](operations.md#backup).

## Rollback

```bash
sudo bash /opt/netprobe/install.sh --rollback                            # neuester Snapshot
sudo bash /opt/netprobe/install.sh --rollback=snapshot_20261005_101500   # bestimmter Snapshot
```

Ein Rollback

1. sichert zuerst den aktuellen Stand als eigenen Snapshot (Anlass `rollback`) – damit
   ist auch der Rollback umkehrbar,
2. stoppt den Dienst, spielt Code, Konfiguration und Datenbank zurück (Integritätsprüfung
   der DB, alte WAL-Dateien werden verworfen),
3. bringt das netprobe-Paket im venv auf den zurückgespielten Stand und startet den Dienst.

`--rollback` ohne Angabe nimmt den neuesten Snapshot, der **nicht** selbst von einem
Rollback stammt. Achtung: Messdaten, die nach dem Snapshot entstanden sind, gehen mit der
zurückgespielten Datenbank verloren.

Das venv wird nicht zurückgespielt. Hatte das Update neue Python-Abhängigkeiten, bleiben
sie installiert; das nächste Update gleicht das über den Hash in `.install-state` aus.

## Fehlerbehebung

| Symptom | Vorgehen |
|---|---|
| `Update fehlgeschlagen … automatischer Rollback` | Ursache steht direkt darüber (z. B. Migrationsfehler). Der vorige Stand läuft wieder; nach Korrektur erneut ausführen |
| `Rollback fehlgeschlagen oder Dienst antwortet nicht` | `journalctl -u netprobe -n 50`; `--rollback=<snapshot>` erneut ausführen |
| `Datenbank-Backup fehlgeschlagen – Abbruch` | DB gesperrt oder Platte voll; es wurde nichts verändert |
| Update meldet immer „Backend-Code geändert“ | Dateien unter `/opt/netprobe/backend` wurden manuell geändert – das Update überschreibt sie (gewollt) |
| `Installiert: unbekannt (ältere Installation)` | Installation mit einem älteren Installer; wird nach dem ersten Lauf gesetzt |
