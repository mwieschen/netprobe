# netprobe als Zentrale für Wachtl-Clients – Plan

Stand: 2026-10-23 · Status: Entwurf

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
nginx (TLS) ─▶ netprobe /api/probe/v1/*   (Kompatibilitätsschicht)
                  │
                  ├─ Standorte, Probes, Checks, Enrollment-Token   (SQLite)
                  ├─ Messwerte: Eingang → 1-min-Aggregate          (SQLite, Retention wie ping_*)
                  └─ Analyse: Zustand, Ausfälle mit Ursache, Korrelation je Standort,
                     Auslöser für netprobe-Diagnose bei Störung/Störungsmeldung
```

## Vertrag (Wachtl 0.9.x, Quelle `server/wachtl/api_probe.py`)

| Endpunkt | Phase | Bemerkung |
| --- | --- | --- |
| `POST /enroll` | 1 | Enrollment-Token → `{probe_id, token, site_name}`; Token = `<probe_id>.<secret>`, nur Hash speichern |
| `GET /config` | 1 | ETag/304; Checks je Probe, Intervalle legt netprobe fest |
| `POST /ingest` | 1 | Prometheus-Text (gzip), `X-Batch-Id` idempotent; nur `check_id` übernehmen, Zeitfenster −8 d/+5 min |
| `POST /heartbeat` | 1 | online/offline, Version, Puffer |
| `POST /deregister` | 1 | Token sofort ungültig |
| `POST /report` | 2 | Störungsmeldung vom Tray → Auslöser für Analyse |
| `POST /findings` | 2 | Client-Hinweise (Kabel, Link, Treiber) |
| `POST /results/path` | 3 | Traceroute/Hops, Pfadwechsel |
| `POST /results/bandwidth` | 3 | iperf3/HTTP-Speedtest |
| `POST /results/rtp` | 3 | Sprachtest (MOS); Reflektor auf netprobe nötig |

Kompatibilität wird mit Vertragstests abgesichert (Request/Response-Beispiele aus Wachtls `docs/API.md` und
`server/tests/test_probe_api.py`). Unterstützte Wachtl-Version wird im Heartbeat geprüft und im UI angezeigt.

## Phasen

**Phase 1 – Grundbetrieb (Ping)**
- Modelle + Migration: `WachtlSite`, `WachtlProbe`, `WachtlCheck`, `WachtlEnrollToken`, `WachtlSample1m`.
- Router `app/api/wachtl_probe.py` (Bearer-Auth, eigene Rate-Limits, Audit für enroll/deregister).
- Admin-API + Seite „Clients“: Standorte, Token erzeugen, Befehlszeile für GPO/Intune mit Server, Pin, Token
  (das vorbelegte Setup baut Wachtl mit Inno Setup – unter Linux nicht möglich, daher Befehlszeile).
- SPKI-Pin aus dem nginx-Zertifikat berechnen und anzeigen.
- Standort-Matrix, Probe-Detail mit Graph (bestehender `TimeSeriesChart`/`TimeRangePicker`).

**Phase 2 – Analyse**
- Ausfälle mit Ursache je Standort: alle Probes eines Standorts betroffen → WAN/Gateway, einzelne → Client/LAN.
- Störungsmeldung oder Ausfall startet optional einen netprobe-Diagnose-Lauf zum betroffenen Ziel
  (`diagnostics_service`) und verknüpft das Ergebnis mit der Meldung; Markierung in den Graphen.
- Client-Hinweise anzeigen.

**Phase 3 – Weitere Check-Arten**: Pfad, Bandbreite (iperf3-Gegenstelle = vorhandener `iperf3_server`), RTP.

**Phase 4 – Benachrichtigung** (offene Entscheidung): E-Mail/Teams würde das bisherige Prinzip
„keine ausgehenden Webhooks/Mails“ aufheben – nur opt-in, nur an selbst konfigurierte Ziele.

## Datenmenge

1-s-Pings vieler Clients passen nicht roh in SQLite (50 Clients × 4 Checks ≈ 17 Mio. Zeilen/Tag). Daher:
- netprobe gibt per `/config` die Intervalle vor (Standard 5 s statt 1 s, je Check einstellbar).
- Beim Ingest direkt zu 1-min-Buckets verdichtet (count, lost, min/avg/max, Jitter); Rohwerte nicht gespeichert.
- 1-min-Buckets 30 Tage, danach in die 5-min-Ebene (365 Tage) – gleiches Muster wie `ping_aggregates`.

## Sicherheit

- Probe-API ist der erste Endpunkt, den Client-PCs erreichen; nginx-Freigabe nur für `/api/probe/v1/`
  aus den Client-Netzen möglich. Bearer-Token nur als Hash, Vergleich zeitkonstant.
- Body-Größen begrenzen (gzip-Bombe), Label/Werte streng parsen, unbekannte `check_id` verwerfen.
- Keine Ausführung von Client-Daten; Hostnamen/Targets aus Client-Daten nie an `proc.py` ohne
  `validate_network_target()`.
