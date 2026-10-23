# netprobe – Sicherheitsmodell

netprobe ist eine lokale Appliance für den Betrieb im vertrauenswürdigen LAN.
Dieses Dokument beschreibt die getroffenen Sicherheitsmaßnahmen und Empfehlungen.

## Betriebsmodell

- Die Web-Anwendung läuft als **nicht-root-Benutzer `netprobe`**.
- Uvicorn/Gunicorn lauscht ausschließlich auf **127.0.0.1:8000** (Loopback).
- **nginx** terminiert den LAN-Verkehr auf Port 80/443 und leitet an das Backend weiter.
- Der systemd-Dienst ist gehärtet (`ProtectSystem=strict`, `PrivateTmp`,
  `NoNewPrivileges`, eingeschränkte `ReadWritePaths`).
- `/opt/netprobe` (Code, venv, `install.sh`) gehört `root` und ist für das Dienstkonto
  nur lesbar. Der Installer führt daraus als root Skripte und venv-Binaries aus; ein
  kompromittierter Dienst kann diese daher nicht manipulieren. Beschreibbar sind nur
  `/var/lib/netprobe` und `/var/log/netprobe`.
- Für Ping-/Capture-Messungen erhält der Dienst **ausschließlich** `CAP_NET_RAW`
  und `CAP_NET_ADMIN` (via `AmbientCapabilities`/`CapabilityBoundingSet`), **keine**
  weiteren Root-Rechte. `HOME` des Dienstes ist `/var/lib/netprobe` (beschreibbar
  nur dort und in `/var/log/netprobe`).

## Authentifizierung & Sessions

- Passwörter werden mit **Argon2** (argon2-cffi) gehasht; Klartext wird nie gespeichert.
- Sessions basieren auf **signierten, zeitlich begrenzten Cookies** (itsdangerous):
  - `HttpOnly` – kein JS-Zugriff.
  - `Secure` – gesetzt, wenn `NETPROBE_HTTPS_ENABLED=true` (TLS über nginx).
  - `SameSite=Lax` – CSRF-Reduktion.
- **Rate Limiting** für Login (Standard: 5 Versuche / 5 Minuten pro IP+Benutzer).
- Beim ersten Start wird ein Admin per CLI (`app.cli ensure-admin`) oder über die
  Umgebungsvariablen `NETPROBE_ADMIN_USER`/`NETPROBE_ADMIN_PASSWORD` angelegt.
  Das Passwort wird **nicht** als Prozessargument übergeben und **nicht geloggt**.

## Rollen (RBAC)

| Rolle    | Rechte                                                              |
|----------|--------------------------------------------------------------------|
| `admin`  | Vollzugriff: Konfiguration, Tests starten/stoppen, Captures, Benutzer |
| `viewer` | Nur Lesezugriff auf Messdaten und Diagramme                        |

Schreibende Endpunkte sind serverseitig mit `require_admin` geschützt; die UI
blendet Aktionen für Viewer aus (Defense-in-Depth, aber die API ist maßgeblich).

## Audit-Log

Sicherheitsrelevante Aktionen werden protokolliert: Login (Erfolg/Fehlschlag),
Rate-Limit-Auslösung, Anlegen/Ändern/Löschen von Zielen, Jobs, Captures und
Benutzern sowie Konfigurationsänderungen. Einsehbar unter *Einstellungen → Audit-Log*.

## Subprozesse & Eingabevalidierung

- **Alle** externen Programme (speedtest, fping/ping, dumpcap, tshark) werden über
  `subprocess` mit **Argumentlisten** und **`shell=False`** gestartet.
- Benutzereingaben werden **niemals** an eine Shell übergeben.
- Interface-Namen, BPF-Capture-Filter und Display-Filter werden gegen konservative
  Whitelists validiert.
- Display-Filter laufen nur gegen vorhandene PCAP-Dateien, mit Timeout, Ergebnis-Paging
  und einem Limit von **1.000 Paketen** pro Abfrage.

## Packet Capture

- Captures werden mit **dumpcap** erstellt, die Auswertung erfolgt mit **tshark**.
- dumpcap wird vom Dienst mit den geerbten Capabilities `cap_net_raw,cap_net_admin`
  ausgeführt (Ambient-Set); für manuelle Nutzung sind zusätzlich die File-Capabilities
  gesetzt und `netprobe` ist in der Gruppe `wireshark`. Die Webanwendung läuft **nicht** als root.
- Capture-Dateien werden **außerhalb des Webroots** unter `/var/lib/netprobe/captures`
  gespeichert. Das Verzeichnis gehört `netprobe:wireshark` mit Modus `2770` (setgid);
  Dateien sind damit für den Dienst und für Administratoren der Gruppe `wireshark`
  zugänglich, nicht jedoch für andere lokale Benutzer.
- Download- und Analyse-Endpunkte validieren, dass der Pfad innerhalb des
  Capture-Verzeichnisses liegt (Schutz vor Path-Traversal).
- Die UI enthält eine **Sicherheitshinweis-Seite**: Captures können vertrauliche Daten
  (Zugangsdaten, interne Hostnamen, unverschlüsselten Verkehr) enthalten.

## Transport (TLS)

`install.sh` richtet HTTPS auf Port 443 ein (`setup_tls`): ein selbstsigniertes Zertifikat
unter `/etc/netprobe/tls/` (`privkey.pem` 0600 root, `fullchain.pem` 0644). Der Schlüssel
wird nur einmal erzeugt; das Zertifikat wird mit demselben Schlüssel erneuert, wenn es
fehlt oder in weniger als 30 Tagen abläuft. Port 80 bleibt für die Weboberfläche aktiv.

Eigenes Zertifikat (Let's Encrypt, interne CA): Dateien ersetzen und nginx neu laden.
**Achtung:** Wachtl-Probes prüfen den SPKI-Pin des Schlüssels – ein neuer Schlüssel macht
alle installierten Probes unbrauchbar, bis sie mit dem neuen Pin neu registriert sind.
Ausschließlich HTTPS: Umleitung 80 → 443 ergänzen und `NETPROBE_HTTPS_ENABLED=true` in
`/etc/netprobe/netprobe.env` setzen (→ Secure-Cookies).

## Wachtl-Probe-API (`/api/probe/v1`)

Der einzige Endpunkt, den Client-PCs erreichen müssen; standardmäßig **abgeschaltet**
(404), Einschalten auf der Seite „Clients“. Details: `docs/wachtl-zentrale.md`.
- Enrollment nur mit Token (Hash gespeichert, begrenzte Nutzungen und Laufzeit, widerrufbar);
  Fehlversuche je Quell-IP rate-limitiert. Probe-Token `<id>.<secret>` nur als SHA-256,
  Vergleich zeitkonstant. Deaktivierte Probes bleiben auch bei Neuinstallation gesperrt.
- Ingest: nur `check_id` wird übernommen (muss der Probe zugewiesen sein), feste
  Metrik-Liste, Zeitfenster −8 Tage/+5 min, Body ≤ 2 MB, entpackt ≤ 8 MB (gzip-Bombe).
- Daten der Probes erreichen nie `proc.py`; Check-Ziele setzt nur der Admin (validiert).
- Enrollment, Abmeldung, Token und Check-Änderungen landen im Audit-Log.

## VoIP-/3CX-Verbindungstest

- Der Test prüft nur die **Erreichbarkeit** (SIP-`OPTIONS`, TLS-Handshake, TCP-Ports,
  ICMP). Es wird **kein Anruf** aufgebaut.
- SIP-**Zugangsdaten** für den optionalen `REGISTER`-Test werden ausschließlich
  transient im RAM für den einen Lauf verarbeitet – **nie** in der Datenbank
  gespeichert, **nie** geloggt, **nicht** als Prozessargument übergeben.
- Über SIP-over-TLS wird `REGISTER` nur bei **erfolgreich verifiziertem** Zertifikat
  gesendet; schlägt die Prüfung fehl, bricht der Registrierungstest ab.
- Der optionale **PCAP-Mitschnitt** eines VoIP-Tests nutzt `dumpcap` mit einem fest
  auf SIP-Ports gesetzten BPF-Filter und schreibt nach `<capture_dir>/voip/`
  (außerhalb des Webroots, gleiche Rechte wie normale Captures). Er kann – abhängig
  vom Interface – vertraulichen Gesprächsverkehr enthalten. Der Roh-PCAP-Download
  (`GET /api/voip/tests/<id>/capture`) ist **admin-only** und wird im Audit-Log
  vermerkt; Viewer sehen ausschließlich die aggregierte JSON-Analyse. Pfad gegen
  `capture_dir` geprüft, Auslieferung als `attachment`. Mitschnitte werden mit dem
  zugehörigen Test-Datensatz nach `NETPROBE_VOIP_RETENTION_DAYS` gelöscht.
- `POST /api/voip/analyze` akzeptiert nur Pfade **innerhalb** von `capture_dir`
  (`relative_to`-Prüfung), keine beliebigen Dateien.
- Der TLS-Erreichbarkeitstest (ohne Zugangsdaten) misst bei Zertifikatsfehlern
  einmalig die Handshake-Zeit auch unverifiziert – dabei werden **keine Daten**
  über die Verbindung gesendet; `tls_verified=false` weist im UI darauf hin.

## Traceroute-Geolocation (Ausnahme vom „keine Cloud"-Prinzip)

- Die Geo-/ASN-Auflösung der Traceroute-Hops ist **hybrid**: zuerst eine lokale
  MaxMind-`.mmdb` (offline), sonst der externe Dienst **ip-api.com** über **HTTP**.
- Dabei verlassen **öffentliche Hop-IP-Adressen** deines Netzpfads die Appliance.
  **Private/Bogon-IPs** (RFC1918, CGNAT 100.64/10, Loopback, Link-Local, Multicast)
  werden **nie** gesendet.
- Ergebnisse werden lokal gecacht (`geoip_cache`, Standard 30 Tage), sodass die
  Zahl der Anfragen minimal bleibt; ein In-Memory-Rate-Limit (40/min) verhindert
  Überschreiten des Free-Limits.
- Vollständig abschaltbar: `NETPROBE_GEOIP_ENABLED=false` (keine Auflösung) oder
  `NETPROBE_GEOIP_ONLINE_ENABLED=false` (nur lokale MaxMind-DB, keine ausgehende
  Verbindung). Ohne Auflösung bleibt der Traceroute voll nutzbar (Hops ohne Geo).
- Die Weltkarte im UI lädt **OpenStreetMap-Kacheln** direkt im Browser des
  Betrachters (`tile.openstreetmap.org`).

## Datenschutz / keine Cloud

- Außer der oben genannten optionalen Geo-Auflösung werden **keine externen
  Cloud-Dienste** verwendet und **keine Telemetrie** gesendet.
  (Der Speedtest kontaktiert Ookla-Messserver – das ist die eigentliche Messung.
  Der VoIP-Test kontaktiert nur die von dir konfigurierte 3CX-Anlage.)
- Alle Messdaten verbleiben lokal in SQLite.

## Empfehlungen

- Betrieb nur im vertrauenswürdigen Management-LAN, nicht direkt am Internet.
- TLS aktivieren und starke Admin-Passwörter verwenden.
- `NETPROBE_SECRET_KEY` zufällig und geheim halten (wird vom Installer erzeugt).
- Regelmäßige Backups (siehe `docs/operations.md`).
