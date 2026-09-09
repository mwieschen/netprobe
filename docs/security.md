# netprobe – Sicherheitsmodell

netprobe ist eine lokale Appliance für den Betrieb im vertrauenswürdigen LAN.
Dieses Dokument beschreibt die getroffenen Sicherheitsmaßnahmen und Empfehlungen.

## Betriebsmodell

- Die Web-Anwendung läuft als **nicht-root-Benutzer `netprobe`**.
- Uvicorn/Gunicorn lauscht ausschließlich auf **127.0.0.1:8000** (Loopback).
- **nginx** terminiert den LAN-Verkehr auf Port 80/443 und leitet an das Backend weiter.
- Der systemd-Dienst ist gehärtet (`ProtectSystem=strict`, `PrivateTmp`,
  `NoNewPrivileges`, eingeschränkte `ReadWritePaths`).
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

Für den produktiven Betrieb wird **HTTPS** empfohlen:
1. Zertifikat/Key unter `/etc/netprobe/tls/` ablegen (Let's Encrypt oder intern signiert).
2. Den TLS-`server`-Block in `deploy/nginx/netprobe.conf` aktivieren.
3. `NETPROBE_HTTPS_ENABLED=true` in `/etc/netprobe/netprobe.env` setzen (→ Secure-Cookies).
4. nginx neu laden.

## VoIP-/3CX-Verbindungstest

- Der Test prüft nur die **Erreichbarkeit** (SIP-`OPTIONS`, TLS-Handshake, TCP-Ports,
  ICMP). Es wird **kein Anruf** aufgebaut.
- SIP-**Zugangsdaten** für den optionalen `REGISTER`-Test werden ausschließlich
  transient im RAM für den einen Lauf verarbeitet – **nie** in der Datenbank
  gespeichert, **nie** geloggt, **nicht** als Prozessargument übergeben.
- Über SIP-over-TLS wird `REGISTER` nur bei **erfolgreich verifiziertem** Zertifikat
  gesendet; schlägt die Prüfung fehl, bricht der Registrierungstest ab.
- Der TLS-Erreichbarkeitstest (ohne Zugangsdaten) misst bei Zertifikatsfehlern
  einmalig die Handshake-Zeit auch unverifiziert – dabei werden **keine Daten**
  über die Verbindung gesendet; `tls_verified=false` weist im UI darauf hin.

## Datenschutz / keine Cloud

- Es werden **keine externen Cloud-Dienste** verwendet und **keine Telemetrie** gesendet.
  (Der Speedtest kontaktiert Ookla-Messserver – das ist die eigentliche Messung.
  Der VoIP-Test kontaktiert nur die von dir konfigurierte 3CX-Anlage.)
- Alle Daten verbleiben lokal in SQLite.

## Empfehlungen

- Betrieb nur im vertrauenswürdigen Management-LAN, nicht direkt am Internet.
- TLS aktivieren und starke Admin-Passwörter verwenden.
- `NETPROBE_SECRET_KEY` zufällig und geheim halten (wird vom Installer erzeugt).
- Regelmäßige Backups (siehe `docs/operations.md`).
