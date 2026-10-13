# netprobe – Installation

netprobe wird mit **einem** Befehl installiert und später mit demselben Befehl
aktualisiert. Der Installer (`install.sh`) erkennt selbst, ob eine Neuinstallation oder
ein Update nötig ist, und arbeitet ohne Rückfragen. Updates beschreibt
[`update.md`](update.md).

## Voraussetzungen

- Ubuntu Server **22.04, 24.04 oder 26.04** (andere Versionen: Warnung, Versuch mit
  Fallbacks)
- root-Rechte (`sudo`)
- Internetzugang für `apt`, PyPI und den Download der Quelle
- mind. **2 GB** frei auf `/`, Port 80 frei (nginx)
- Python ≥ 3.12 (bringen alle unterstützten Ubuntu-Versionen mit)

## Ein-Befehl-Installation

```bash
curl -sSL https://raw.githubusercontent.com/youruser/netprobe/main/install.sh | sudo bash
```

> `youruser/netprobe` ist ein Platzhalter: im Befehl **und** als Default von
> `NETPROBE_REPO` in `install.sh` durch das echte Repository ersetzen.

Woher die Quelle kommt:

1. **Neuestes GitHub-Release** – Asset `netprobe.tar.gz` (von
   `scripts/build-release.sh` erzeugt, Frontend vorgebaut → **kein Node.js** auf dem
   Server).
2. Gibt es kein Release: **Quellarchiv des Branches `main`**. Das Frontend wird dann auf
   dem Server gebaut; Node.js ≥ 20 wird automatisch installiert.

Die Quelle wird nach `/tmp/netprobe-install.*` geladen und nach dem Lauf gelöscht.

### Ablauf der Neuinstallation

Der Installer zeigt jeden Schritt als `==> [n/11] …` an:

| Schritt | Inhalt |
|---|---|
| 1 Vorbedingungen | Ubuntu-Version, Internet, freier Speicher, Port-80-Konflikt |
| 2 Systempakete | nur fehlende Pakete; Speedtest-CLI (Ookla-Repo, sonst Tarball); Node.js nur bei Bedarf |
| 3 Benutzer und Verzeichnisse | Systembenutzer `netprobe`, `/var/lib/netprobe`, Capture-Verzeichnis, Capabilities |
| 4 Konfiguration | `/etc/netprobe/netprobe.env` mit zufälligem `SECRET_KEY` (bestehende Datei bleibt) |
| 5 Backend | Code nach `/opt/netprobe/backend`, venv, Python-Abhängigkeiten (inkl. WeasyPrint, falls möglich) |
| 6 Frontend | vorgebautes `dist` übernehmen oder bauen (prüft vorher ~1 GB freien Platz in `/tmp`) |
| 7 Datenbank | `alembic upgrade head`, danach Prüfung `alembic current` = `heads` |
| 8 Admin-Benutzer | nur, wenn noch kein Benutzer existiert |
| 9 systemd | `netprobe.service` aktivieren und starten; `netprobe-iperf3.service` installiert (inaktiv) |
| 10 nginx & Co. | nginx-Site, logrotate, GeoIP (optional), UFW-Regeln 80/443 **nur wenn UFW aktiv ist** |
| 11 Smoke-Tests | Backend-Healthcheck (wartet bis 30 s) und nginx-Proxy |

Zum Schluss erscheint eine Zusammenfassung mit URL:

```
==============================================================
 netprobe 1.0.0 (5e59739, 2026-10-05) wurde installiert.
==============================================================

  Initialer Admin (Passwort wird NUR JETZT angezeigt – bitte sofort ändern):
    Benutzer:     admin
    Passwort:     kadXzJBkFOrC16Xe
    …
  Weboberfläche:  http://192.168.1.50/
  API-Dokumentation: http://192.168.1.50/api/docs
```

### Admin-Benutzer

Ohne Vorgabe legt der Installer `admin` mit einem **zufälligen Passwort** an. Es wird
direkt nach dem Anlegen und noch einmal in der Zusammenfassung angezeigt, aber nirgends
gespeichert oder geloggt. Eigenes Passwort vorgeben (Variablen hinter `sudo` setzen,
sonst entfernt `sudo` sie):

```bash
curl -sSL …/install.sh | sudo NETPROBE_ADMIN_USER=admin NETPROBE_ADMIN_PASSWORD='mind8Zeichen' bash
```

Passwort vergessen oder neu setzen:

```bash
cd /opt/netprobe/backend && sudo -u netprobe env NETPROBE_ENV_FILE=/etc/netprobe/netprobe.env \
    /opt/netprobe/venv/bin/python -m app.cli create-admin --username admin --force
```

### Interaktiv installieren

`--interactive` fragt wie früher Admin-Name/-Passwort, die UFW-Regel und die
Netzwerkkonfiguration (unverändert / DHCP / statisch, nur per `netplan try`) ab. Bei
`curl | bash` liest der Installer die Eingaben vom Terminal (`/dev/tty`):

```bash
curl -sSL …/install.sh | sudo bash -s -- --interactive
```

## Installation ohne GitHub-Zugriff (Release-Paket)

```bash
# Entwicklungsrechner (Linux/macOS oder Windows mit Git Bash):
bash scripts/build-release.sh          # -> release/netprobe-<version>-<commit>.tar.gz
scp release/netprobe-*.tar.gz <user>@<server>:

# Server:
tar xzf netprobe-*.tar.gz && cd netprobe-*/
sudo bash install.sh
```

Läuft `install.sh` aus einem netprobe-Verzeichnis (Release-Paket mit `RELEASE`-Datei
oder Repository-Checkout), wird **diese** Quelle verwendet und nichts heruntergeladen.
Aus einem Repository-Checkout wird das Frontend auf dem Server gebaut.

Release für die Ein-Befehl-Installation veröffentlichen: `build-release.sh` legt
zusätzlich `release/netprobe.tar.gz` an – dieses Asset an ein GitHub-Release hängen:

```bash
gh release create v1.0.0 release/netprobe.tar.gz
```

## Optionen

Bei `curl | bash` Optionen nach `bash -s --` angeben: `… | sudo bash -s -- --force`.

| Option | Wirkung |
|---|---|
| *(keine)* | automatisch: Neuinstallation oder Smart-Update |
| `--rebuild-frontend` | Frontend erzwingen (bauen bzw. `dist` neu einspielen) |
| `--run-migrations` | `alembic upgrade head` erzwingen (mit Prüfung) |
| `--backup-only` | nur Snapshot anlegen, nichts ändern |
| `--rollback[=DIR]` | neuesten (oder angegebenen) Snapshot zurückspielen |
| `--list-backups` | Snapshots anzeigen |
| `--force` | beim Update alle Komponenten neu einspielen |
| `--update` | Update erzwingen (Fehler, wenn nichts installiert ist) |
| `--install` | vollständige Neueinrichtung über eine bestehende Installation: Snapshot, venv neu, alle Schritte; Daten und Konfiguration bleiben (Löschen nur mit `--interactive` nach Eingabe von `LOESCHEN`) |
| `--interactive` | Admin, Firewall und Netzwerk abfragen |
| `-h`, `--help` | Hilfe |

## Umgebungsvariablen

| Variable | Default | Bedeutung |
|---|---|---|
| `NETPROBE_REPO` | `youruser/netprobe` | GitHub-Repository `<owner>/<repo>` |
| `NETPROBE_REF` | *(leer)* | Branch, Tag oder Commit statt „neuestes Release“ (Quellarchiv → Frontend wird gebaut) |
| `NETPROBE_SOURCE_URL` | *(leer)* | beliebige tar.gz-URL (Release-Paket oder Quellarchiv), z. B. interner Mirror |
| `NETPROBE_ADMIN_USER` | `admin` | initialer Admin |
| `NETPROBE_ADMIN_PASSWORD` | *(zufällig)* | initiales Passwort (mind. 8 Zeichen) |
| `NETPROBE_KEEP_SNAPSHOTS` | `5` | Anzahl aufbewahrter Snapshots |
| `NETPROBE_MAXMIND_LICENSE_KEY` | *(leer)* | lädt GeoLite2-City/-ASN für die Traceroute-Geolokalisierung |

## Was wo liegt

| Pfad | Inhalt |
|---|---|
| `/opt/netprobe/backend`, `frontend/dist`, `venv` | Anwendung |
| `/opt/netprobe/install.sh` | Kopie des Installers (Update/Rollback ohne Quelle) |
| `/opt/netprobe/.install-state/` | installierte Version, Hash der Python-Abhängigkeiten, Hash der Frontend-Quellen, Hashes der Konfigurationsdateien |
| `/etc/netprobe/netprobe.env` | Konfiguration (wird nie überschrieben) |
| `/var/lib/netprobe/` | Datenbank, Captures, Reports, Snapshots (`backups/snapshot_*`) |
| `/var/log/netprobe/` | Logs |

## Fehlerbehebung

| Symptom | Ursache / Lösung |
|---|---|
| `netprobe konnte nicht geladen werden` | Repository falsch oder privat → `NETPROBE_REPO` bzw. `NETPROBE_SOURCE_URL` setzen |
| Abbruch mitten in der Neuinstallation | Ursache beheben und den Befehl erneut ausführen – fertige Schritte werden erkannt, fehlende ergänzt |
| `Zu wenig freier Speicher für den Frontend-Build` | `/tmp` zu klein → Release-Paket nutzen (kein Build) oder `TMPDIR=/var/tmp` setzen |
| `--interactive benötigt ein Terminal` | ohne TTY aufgerufen (z. B. aus CI) → ohne `--interactive` und mit `NETPROBE_ADMIN_PASSWORD` |
| Weboberfläche nicht erreichbar, UFW aktiv | Installer öffnet 80/443 nur, wenn UFW beim Lauf schon aktiv war → `sudo ufw allow 80/tcp` |

Weitere Betriebsthemen (Backup per cron, Restore einzelner DB-Backups, Capabilities,
Deinstallation): [`operations.md`](operations.md).
