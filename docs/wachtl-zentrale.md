# netprobe als Zentrale für Wachtl-Clients

Stand: 2026-10-23 · Phase 1 (Ping), Phase 2 (Meldungen, Hinweise, Ausfallanalyse, Diagnose) und Phase 3
(Pfad, Bandbreite, Sprache) und Phase 4 (E-Mail-Benachrichtigung) umgesetzt

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
   WachtlSetup-0.9.6.exe /VERYSILENT /SUPPRESSMSGBOXES /Server=https://netprobe.kunde.local /Pin="…" /Token=wachtl-…
   ```

   Per GPO/Intune/Softwareverteilung auf die PCs (Schritt für Schritt: `ANLEITUNG.md`). Das unveränderte Setup
   der jeweils neuesten Wachtl-Version steckt im Release-Paket (`clients/wachtl/`, von
   `scripts/build-release.sh` aus `releases/<Version>/` des Wachtl-Repositorys geladen und gegen dessen
   `SHA256SUMS.txt` geprüft); `install.sh` legt es unter `/opt/netprobe/clients/wachtl/` ab
   (`NETPROBE_WACHTL_CLIENT_DIR`), Download unter *Einrichtung* (`GET /api/clients/setup`, Admin). Das
   vorbelegte Setup aus dem Wachtl-Web-UI gibt es hier nicht – es wird mit Inno Setup unter Windows gebaut.
   Ab Wachtl 0.9.3 legt das Setup das Server-Zertifikat als vertrauenswürdig ab (`wachtl-probe.exe trust`); die
   Wachtl-CA unter `/zertifikat/wachtl.crt` gibt es bei netprobe nicht, die Probe nimmt dann das selbstsignierte
   netprobe-Zertifikat (passt zum Pin).
5. Nach etwa 30 Sekunden erscheint der PC in der Standort-Matrix.

Mehrere Server-Adressen (Name und IP, falls die Namensauflösung am Standort fehlt):
`NETPROBE_WACHTL_SERVER_URLS=https://netprobe.kunde.local,https://10.0.0.5` in `/etc/netprobe/netprobe.env`.
Die Probes übernehmen die Liste per `/config`.

## Vertrag (Wachtl 0.9.x, Quelle `server/wachtl/api_probe.py`)

| Endpunkt | Stand | Bemerkung |
| --- | --- | --- |
| `POST /enroll` | ✅ Phase 1 | Enrollment-Token → `{probe_id, token, site_name}`; Token `<probe_id>.<secret>`, nur Hash gespeichert |
| `GET /config` | ✅ Phase 1–3 | ETag/304; alle Check-Typen mit `peer` (Gegenstelle), `iperf_server`/`rtp_reflector` je Probe, `client_findings` |
| `POST /ingest` | ✅ Phase 1/3 | Prometheus-Text (gzip), `X-Batch-Id` idempotent (9 Tage); `wachtl_ping_*` für Ping-, `wachtl_hop_*` (mit `hop`/`hop_ip`) für Pfad-Checks |
| `POST /heartbeat` | ✅ Phase 1–3 | online/offline, Version, Puffer, Adressen; `commands` = offene Aufträge (`run_check`/`retrace`, 1 h gültig); Dienst-Neustart schließt alte aktive Hinweise |
| `POST /deregister` | ✅ Phase 1 | Token sofort ungültig, Messwerte bleiben |
| `POST /report` | ✅ Phase 2 | Störungsmeldung vom Tray; 201 neu / 200 dieselbe `report_id`; startet optional eine Diagnose |
| `POST /findings` | ✅ Phase 2 | Client-Hinweise (Kabel, Link, Treiber), Upsert je `finding_id`, höchstens 50 je Sendung |
| `POST /results/path` | ✅ Phase 3 | Traceroute; gleicher Pfad schreibt die letzte Zeile fort, Wechsel (stille Hops zählen nicht) → Ereignis |
| `POST /results/bandwidth` | ✅ Phase 3 | iperf3/WAN je Richtung; unter Schwellwert oder Fehler → Ereignis |
| `POST /results/rtp` | ✅ Phase 3 | Sprachtest mit MOS; unter `min_mos` oder Fehler → Ereignis |

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

## Pfad, Bandbreite und Sprache (Phase 3)

Check-Typen wie in Wachtl, Felder und Standardwerte an einer Stelle (`app/services/wachtl_checktypes.py`,
`GET /api/clients/check-types`); das Formular im UI entsteht daraus.

| Typ | Messung | Ansicht |
| --- | --- | --- |
| **Pfad** | Traceroute alle `trace_interval_min`, Dauerpings auf jeden Hop (`interval_s`) | MTR-Tabelle mit Bewertung, Pfadverlauf |
| **Bandbreite intern** | iperf3 zu netprobe, zu einem anderen PC oder eigenem Server | Durchsatz-Graph, Messungen |
| **Bandbreite WAN** | HTTP-Download/-Upload (Standard: Cloudflare) oder iperf3 im Internet | wie oben |
| **Sprache** | RTP-Strom wie ein Telefonat (G.711/G.729) zu einem Reflektor, MOS nach E-Modell | MOS-Graph, Tests |

- **MTR-Bewertung** (`wachtl_results.evaluate_hops`): Verlust nur an einem Hop, spätere Hops deutlich weniger →
  *ICMP-Drosselung* (kein Problem); Verlust, der bis zum Ziel bleibt → *Verlust beginnt hier* (und *Folge* für die
  späteren Hops); Laufzeit springt um > 30 ms und bleibt → *Laufzeit steigt hier*. Hop-Pings werden wie Pings zu
  1-min-/1-h-Buckets verdichtet (`wachtl_hop_samples`).
- **Gegenstellen**: „netprobe“ = vorhandener iperf3-Server (Seite *iperf3*) bzw. der **RTP-Reflektor**
  (`app/services/rtp_reflector.py`, UDP 5004, an/aus unter *Clients → Einrichtung*). Der Reflektor antwortet nur
  auf Wachtl-RTP-Pakete, in gleicher Größe, mit Ratenbegrenzung (2000 Pakete/s je Absender, 20 000 gesamt) –
  kein Verstärker. Adresse für die Probes: `NETPROBE_WACHTL_PEER_HOST`, sonst der Host, über den die Probe
  netprobe erreicht. „andere Probe“: den PC unter *Probes → Gegenstelle* freigeben (iperf3 5201/RTP 5004 auf dem PC).
  **Firewall**: TCP 5201 und UDP 5004 von den Client-Netzen zu netprobe freigeben – `install.sh` öffnet diese
  Ports nicht selbst.
- **Zeitplan** je Check (alle N min, Versatz je PC automatisch verteilt, Zeitfenster, Wochentage); „Jetzt messen“
  bzw. „Pfad neu ermitteln“ geht mit dem nächsten Heartbeat (≤ 30 s) an den PC.
- **Ereignisse** (Tabelle `wachtl_events`): Pfadwechsel, Ziel beim Traceroute nicht erreicht,
  Bandbreite unter Schwellwert, Bandbreiten-/Sprachtest fehlgeschlagen, MOS unter `min_mos` (unter 3,1 kritisch).
  `notified_at` ist für die Benachrichtigung (Phase 4) vorgesehen.
- Ausfallanalyse und Matrix-Ampel für Ping bleiben unverändert; Pfad/Bandbreite/Sprache zeigen in der Matrix die
  letzte Messung (7 Tage). Ergebnisse werden `NETPROBE_WACHTL_RESULT_RETENTION_DAYS` (365) aufbewahrt.

## E-Mail-Benachrichtigung (Phase 4)

Einrichtung im Web-UI unter **Clients (Wachtl) → Benachrichtigung (E-Mail)** (nur Admins):

- **SMTP-Server**: Host, Port, Verschlüsselung (STARTTLS 587, SSL/TLS 465, keine – nur internes Relay),
  Benutzer/Passwort (leer = ohne Anmeldung), Absender, Zertifikatsprüfung (Standard an; aus nur für interne
  Server mit selbstsigniertem Zertifikat – die Oberfläche warnt). **Testmail** an eine beliebige Adresse.
- **Empfänger**: Name, Adresse, Standort (oder alle), Mindest-Schwere (ab Info / ab Warnung / nur kritisch) und
  was sie erhalten: Störungen & PCs offline, Entwarnungen, Ereignisse, Störungsmeldungen der Benutzer.
- **Grenzwerte**: Störung melden ab N Minuten Dauer (2), PC offline nach N Minuten ohne Kontakt (15),
  höchstens N Mails je Empfänger und Stunde (20), Adresse von netprobe für den Link in der Mail.

**Ablauf**: Der Scheduler-Job `wachtl_notify` läuft jede Minute (auch bei pausierter Automatisierung) und
gleicht die aktuellen Lagen mit `wachtl_alert_states` ab – jede Störung und jeder Offline-PC wird genau einmal
gemeldet, das Ende als **Entwarnung** (mit Dauer). Dazu kommen neue Ereignisse (`wachtl_events.notified_at`,
höchstens 1 h alt) und Störungsmeldungen. Jeder Empfänger erhält **eine Sammel-Mail** je Lauf; Betreff mit
„KRITISCH“, wenn etwas Kritisches dabei ist. Ist die Benachrichtigung aus, werden die Zustände trotzdem
fortgeschrieben – beim Einschalten kommt keine Mailflut über Vergangenes. Versandprotokoll im UI.

| Schwere | Beispiele |
| --- | --- |
| kritisch | ganzer Standort ohne Gateway oder Internet; MOS unter 3,1 |
| Warnung | einzelner PC mit Gateway-/WAN-/Ziel-Störung; PC offline; Bandbreite/MOS unter Schwellwert; Messfehler; Störungsmeldung |
| Info | Pfadwechsel; Messlücke eines einzelnen PCs |

**Passwort**: verschlüsselt im KV-Store (Fernet, Schlüssel aus `NETPROBE_SECRET_KEY` abgeleitet), nie über die
API zurückgegeben, nie im Log/Audit. Wird `NETPROBE_SECRET_KEY` geändert, ist es nicht mehr lesbar – die
Oberfläche meldet das, Passwort neu eingeben. Neue Abhängigkeit: `cryptography` (installiert `install.sh` beim
Update automatisch, weil sich `pyproject.toml` geändert hat).

Mit Phase 4 verschickt netprobe **erstmals selbst E-Mails** – nur an den eingetragenen SMTP-Server und die
eingetragenen Empfänger, nur wenn eingeschaltet. Die übrigen Funktionen (z. B. Retransmission-Schwellwerte)
bleiben ohne ausgehende Benachrichtigung.
