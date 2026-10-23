# netprobe als Zentrale für Wachtl-Clients

Stand: 2026-10-23 · Phase 1 (Ping) und Phase 2 (Meldungen, Hinweise, Ausfallanalyse, Diagnose) umgesetzt

## Ziel

Wachtl-Probes (Windows-Dienst, [fkroczak/Wachtl](https://github.com/fkroczak/Wachtl), MIT) werden auf die
Client-PCs verteilt und melden an **netprobe** als zentralen Server. netprobe verwaltet Standorte, Probes und
Checks, speichert und bewertet die Messwerte und verknüpft sie mit den eigenen Tiefenanalysen (Diagnose,
Mitschnitt, Retransmissions, SIP, Berichte).

Randbedingung: **Am Wachtl-Code wird nichts geändert.** netprobe implementiert die Gegenseite der
Probe-API v1 kompatibel, die Clients laufen als unverändertes Wachtl-Release.

```
Windows-PCs: WachtlSetup /Server=https://netprobe… /Pin=… /Token=…
        │ HTTPS 443, nur ausgehend, SPKI-Pin
        ▼
nginx (TLS) ─▶ netprobe /api/probe/v1/*   (app/api/wachtl_probe.py)
                  │
                  ├─ Standorte, Probes, Checks, Enrollment-Token   (wachtl_* Tabellen)
                  ├─ Messwerte: Ingest → 1-min-Buckets → nach 14 Tagen 1-h-Buckets (365 Tage)
                  └─ Seite „Clients“: Standort-Matrix, Probe-Detail mit Graph, Verwaltung
```

## Einrichtung

1. **Installieren/aktualisieren** mit `install.sh`. Es legt unter `/etc/netprobe/tls/` einen Schlüssel und ein
   selbstsigniertes Zertifikat an und aktiviert HTTPS (Port 443) in nginx. Firewall: TCP 443 von den
   Client-Netzen zu netprobe.
2. In netprobe **Clients (Wachtl)** öffnen, **Anbindung aktiv** einschalten. Unter *Einrichtung* steht der
   SPKI-Pin.
3. **Standort** anlegen, **Checks** anlegen (z. B. `gateway` als Gateway, `8.8.8.8` und `1.1.1.1` als
   Internet-Referenz, ein internes Ziel). Ohne Standort gilt ein Check für alle Standorte.
4. **Token erzeugen** (Anzahl Installationen, Gültigkeit). Angezeigt werden Token und der fertige Befehl:

   ```powershell
   WachtlSetup-0.9.2.exe /VERYSILENT /SUPPRESSMSGBOXES /Server=https://netprobe.kunde.local /Pin="…" /Token=wachtl-…
   ```

   Per GPO/Intune/Softwareverteilung auf die PCs. Das Setup stammt aus dem Wachtl-Repository
   (`releases/<Version>/`). Das vorbelegte Setup aus dem Wachtl-Web-UI gibt es hier nicht – es wird mit
   Inno Setup unter Windows gebaut.
5. Nach etwa 30 Sekunden erscheint der PC in der Standort-Matrix.

Mehrere Server-Adressen (Name und IP, falls die Namensauflösung am Standort fehlt):
`NETPROBE_WACHTL_SERVER_URLS=https://netprobe.kunde.local,https://10.0.0.5` in `/etc/netprobe/netprobe.env`.
Die Probes übernehmen die Liste per `/config`.

## Vertrag (Wachtl 0.9.x, Quelle `server/wachtl/api_probe.py`)

| Endpunkt | Stand | Bemerkung |
| --- | --- | --- |
| `POST /enroll` | ✅ Phase 1 | Enrollment-Token → `{probe_id, token, site_name}`; Token `<probe_id>.<secret>`, nur Hash gespeichert |
| `GET /config` | ✅ Phase 1 | ETag/304; nur Ping-Checks; `client_findings` an (Phase 2), iperf3-Gegenstelle und RTP-Reflektor aus |
| `POST /ingest` | ✅ Phase 1 | Prometheus-Text (gzip), `X-Batch-Id` idempotent (9 Tage); `wachtl_ping_up/rtt_ms/ttl/jitter_ms` |
| `POST /heartbeat` | ✅ Phase 1 | online/offline, Version, Puffer, Adressen; `commands` immer leer; Dienst-Neustart (Uptime kleiner) schließt alte aktive Hinweise |
| `POST /deregister` | ✅ Phase 1 | Token sofort ungültig, Messwerte bleiben |
| `POST /report` | ✅ Phase 2 | Störungsmeldung vom Tray; 201 neu / 200 dieselbe `report_id`; startet optional eine Diagnose |
| `POST /findings` | ✅ Phase 2 | Client-Hinweise (Kabel, Link, Treiber), Upsert je `finding_id`, höchstens 50 je Sendung |
| `POST /results/path` | Phase 3 | Traceroute/Hops, Pfadwechsel |
| `POST /results/bandwidth` | Phase 3 | iperf3/HTTP-Speedtest |
| `POST /results/rtp` | Phase 3 | Sprachtest (MOS); Reflektor auf netprobe nötig |

Validierung, Kategorien und Texte wie Wachtl (`app/services/wachtl_events.py`). Ein behobener Hinweis wird durch
eine ältere Nachlieferung nicht wieder aktiv; beim Beheben bleiben Problem und Lösungsvorschlag stehen.

## Datenmenge

- Check-Intervall mindestens `NETPROBE_WACHTL_MIN_INTERVAL_S` (5 s); Wachtl selbst erlaubt 1 s.
- Keine Rohwerte: Ingest verdichtet direkt zu 1-min-Buckets (`wachtl_samples`, `step_s=60`: Anzahl, Verlust,
  RTT min/max/Summe, Jitter-Summe, TTL). Nach `NETPROBE_WACHTL_MINUTE_RETENTION_DAYS` (14) fasst der
  Housekeeping-Job sie zu 1-h-Buckets zusammen (`step_s=3600`), gelöscht nach
  `NETPROBE_WACHTL_RETENTION_DAYS` (365).
- Größenordnung: 50 PCs × 4 Checks ≈ 290 000 Zeilen/Tag in den ersten 14 Tagen, danach 4 800/Tag.

## Zustand in der Matrix

Aus den letzten 5 Minuten je Probe und Check: **gestört** (alle Pings verloren), **auffällig** (mindestens 2 verlorene Pings
und Verlust ≥ `report_ping_loss_warn_pct`, oder RTT ø > `diagnostics_ping_latency_warn_ms`), **OK**, **keine Daten**
(auch wenn die Probe offline ist: kein Heartbeat seit 90 s).

## Störungen mit Ursache (Phase 2)

`app/services/wachtl_analysis.py` (rein) bewertet je PC und Minute; ein Check gilt als gestört ab
`NETPROBE_WACHTL_OUTAGE_LOSS_PCT` (25 %) Verlust in der Minute:

| Ursache | Erkennbar an |
| --- | --- |
| **Gateway/LAN** | ein Check mit Rolle *Gateway* gestört |
| **WAN/Internet** | Gateway in Ordnung, aber *alle* Internet-Referenzen gestört |
| **Ziel** | nur einzelne Ziele/Referenzen gestört |
| **Keine Messwerte** | Lücke ohne jede Messung zwischen zwei Minuten mit Daten (ab 2 Intervallen, mind. 2 min) |

- Sind in einer Minute **alle** (mindestens zwei) PCs eines Standorts mit derselben Ursache Gateway/LAN, WAN
  oder Keine Messwerte betroffen, wird daraus eine Störung **des Standorts**; die Einzelstörungen entfallen.
- Gestörte Minuten werden zu **Episoden** verbunden (bis zu 2 ungestörte Minuten dazwischen). Ursache ist die
  häufigste der Episode, bei Gleichstand LAN vor WAN vor Ziel – schwankender Paketverlust ergibt so eine
  Störung statt Dutzender, die zwischen WAN und Ziel springen.
- Minutenauflösung, höchstens `NETPROBE_WACHTL_INCIDENT_MAX_DAYS` (14) zurück; berechnet bei Abruf
  (`GET /api/clients/incidents`), nicht gespeichert.

## Diagnose bei Störungsmeldung (Phase 2)

Eine neue Störungsmeldung startet – wenn `NETPROBE_WACHTL_REPORT_DIAGNOSTICS` (an) – im Hintergrund einen
netprobe-Diagnose-Lauf (Ping, MTU, Traceroute) **von netprobe aus** zum PC (LAN-Adresse aus dem Heartbeat,
sonst Absenderadresse) und zu den Zielen/Referenzen seines Standorts (ohne `gateway`). Höchstens einer je
Standort in `NETPROBE_WACHTL_DIAGNOSTICS_COOLDOWN_MIN` (10); der Lauf wird an der Meldung vermerkt und ist
unter *Netzwerk-Diagnose* (`/diagnostics?run=<id>`) zu sehen. Manuell: „Diagnose“ an der Meldung bzw.
„Diagnose starten“ auf der PC-Seite (Admin, ohne Sperrzeit).

Meldungen erscheinen auf der Clients-Seite (offen/alle, „Erledigt“ mit Notiz) und als gestrichelte Markierung
im Graphen des PCs. Meldungen und behobene Hinweise werden nach `NETPROBE_WACHTL_EVENT_RETENTION_DAYS` (365)
gelöscht.

## Nächste Phasen

**Phase 3 – Weitere Check-Arten**: Pfad, Bandbreite (iperf3-Gegenstelle = vorhandener `iperf3_server`), RTP.

**Phase 4 – Benachrichtigung** (offen): E-Mail/Teams würde das Prinzip „keine ausgehenden Webhooks/Mails“
aufheben – nur opt-in, nur an selbst konfigurierte Ziele.
