# Network Diagnostics

## Hintergrund

Ein Netzwerk-Check besteht selten aus einem einzelnen Test – meist will man
Ping, Retransmissions, MTU und Traceroute zu denselben Zielen gemeinsam sehen,
um ein Muster zu erkennen (z. B. "nur Ping schlecht, aber MTU/Traceroute
unauffällig" vs. "alles schlecht"). *Network Diagnostics* bündelt bis zu 10
frei definierbare Ziele und sechs Tests zu einem Lauf und zeigt eine
Ampel-Übersicht (OK/WARN/KRITISCH) je Ziel und gesamt.

**Wichtig:** Diese Seite implementiert keine neue Test-Logik. Jeder Sub-Test
ruft den bestehenden, bereits eigenständig getesteten Service auf:

| Sub-Test | Ruft auf | Persistiert |
|----------|----------|-------------|
| Ping | `wan_service.measure()` | nein (nur im Diagnose-Snapshot) |
| TCP-Retransmissions | `retransmission_service.run_test(mode="active")` – **ein** gemeinsamer Mitschnitt für alle Ziele | ja, als normaler `RetransmissionTest` (siehe [`docs/retransmissions.md`](retransmissions.md)) |
| MTU-Pfad-Test | `mtu_service.discover_path_mtu()` | ja, als normaler `MtuTest` |
| Broadcast-Analyse | `capture_service.analyze()` auf einen kurzen Mitschnitt (interface-weit, nicht pro Ziel) | nein |
| Traceroute | `traceroute_service.run()` | ja, als normaler `Traceroute` |
| Speedtest | `speedtest_service.run_speedtest()` – läuft **einmal pro Lauf**, nicht pro Ziel | ja, als normaler `SpeedtestResult` |

Retransmission-, MTU-, Traceroute- und Speedtest-Ergebnisse tauchen deshalb
zusätzlich in der jeweiligen Feature-Seite/-Historie auf – das ist beabsichtigt.
Für Ping gibt es keine Ad-hoc-Persistenz (`WanResult` erfordert ein gespeichertes
`WanTarget`), daher bleibt das Ping-Ergebnis ausschließlich im Diagnose-Snapshot
erhalten.

## Ampel-Klassifikation

Je Ziel werden dieselben Schwellwerte wie in den jeweiligen Einzel-Features
verwendet (keine zweite, potenziell abweichende Schwellwert-Quelle):

| Problem | Schwellwert-Setting |
|---------|---------------------|
| Hoher Paketverlust | `NETPROBE_REPORT_PING_LOSS_WARN_PCT` (1 %) |
| Hoher Jitter | `NETPROBE_REPORT_JITTER_WARN_MS` (30 ms) |
| Hohe Latenz | `NETPROBE_DIAGNOSTICS_PING_LATENCY_WARN_MS` (100 ms, neu – es gab noch keinen Latenz-Schwellwert) |
| Erhöhte/kritische Retransmission-Rate | `NETPROBE_RETRANS_WARN_THRESHOLD_PCT` / `NETPROBE_RETRANS_ALERT_THRESHOLD_PCT` |
| Niedrige Pfad-MTU | `NETPROBE_REPORT_MTU_INFO_BELOW` (1500) |
| Hoher Broadcast-/Multicast-Anteil | `NETPROBE_ANALYSIS_BROADCAST_WARN_PCT` (10 %) |

Ein Ziel ist `KRITISCH`, wenn die Retransmission-Rate den Alarm-Schwellwert
überschreitet oder mindestens drei Probleme gleichzeitig auftreten; sonst `WARN`
bei mindestens einem Problem, sonst `OK`. Der Gesamtstatus eines Laufs ist der
schlechteste Ziel-Status, zusätzlich eskaliert ein hoher Broadcast-Anteil oder
ein komplett fehlgeschlagener Retransmission-Sub-Test (z. B. fehlendes
`dumpcap`) mindestens auf `WARN` – ein explizit ausgewählter, aber nie
gelaufener Test bleibt so sichtbar statt kommentarlos zu fehlen.

## Grenzen

- Maximal 10 Ziele je Lauf (`NETPROBE_DIAGNOSTICS_MAX_TARGETS`), geteilt mit dem
  Limit für aktive Retransmission-Tests.
- Diagnose-Ziele haben keinen Port (anders als die dedizierte
  Retransmissions-Seite); der aktive TCP-Test verwendet für alle Ziele Port 443.
- Broadcast-Analyse ist **interface-weit**, nicht pro Ziel (Broadcast-Traffic
  hat keinen sinnvollen Bezug zu einem einzelnen Unicast-Ziel).
- Speedtest läuft **einmal pro Lauf**, unabhängig von der Anzahl der Ziele.
- Es gibt keine separate Lauf-Historien-Tabelle für die Sub-Tests – nur einen
  JSON-Snapshot je `DiagnosticRun` (wie bei `Report.data_json`). Einzelergebnisse
  bleiben in ihren eigenen Tabellen/Seiten auffindbar.
