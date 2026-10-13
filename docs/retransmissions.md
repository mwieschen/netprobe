# TCP-Retransmission-Monitoring

## Hintergrund

TCP-Retransmissions sind erneut gesendete Segmente, deren Bestätigung (ACK) nicht
rechtzeitig eintraf. Vereinzelt sind sie normal; eine dauerhaft erhöhte Rate deutet
auf Paketverlust oder Überlastung hin – im lokalen Netzwerk, auf der
WAN-Anbindung oder bei einem einzelnen entfernten Server.

netprobe erkennt Retransmissions auf zwei Arten:

- **Passiv**: ein kurzer `dumpcap`-Mitschnitt auf einem Interface (Standard: 60 s)
  wird per `tshark` (`tcp.analysis.retransmission`) ausgewertet. Betroffene Ziele
  werden automatisch aus dem beobachteten Traffic ermittelt.
- **Aktiv**: zu frei definierbaren Zielen (IP/Hostname + Port, max. 10) werden
  gezielt TCP-Connect-Proben gesendet (reines `socket.connect()` ohne Payload,
  nur der TCP-Handshake – funktioniert protokollunabhängig für jeden TCP-Port),
  während parallel ein auf diese Ziele gefilterter Mitschnitt läuft. Jedes
  konfigurierte Ziel erscheint im Ergebnis, auch bei 0 Retransmissions/0 Paketen
  (macht einen vollständigen Verbindungsausfall sichtbar) – zusätzlich zu den
  Retransmission-Kennzahlen werden Verbindungsversuche, -fehler und mittlere
  Connect-Latenz erfasst. Ziele können parallel (`concurrent: true`, Standard)
  oder nacheinander getestet werden; die Gesamtdauer (`duration_s`) bleibt dabei
  gleich – bei "nacheinander" wird sie gleichmäßig auf die Ziele aufgeteilt.
  Häufig genutzte Ziele lassen sich als Preset speichern (Name, Host, Port,
  Beschreibung) und per Klick wiederverwenden.

In beiden Fällen wird die PCAP-Datei nach der Analyse gelöscht – gespeichert
wird nur die Auswertung (Kennzahlen, betroffene Ziele, Zeitreihe).

Nur **TCP** wird unterstützt (Retransmissions im Sinn von
`tcp.analysis.retransmission` sind ein TCP-Konzept); UDP-Paketverlust wird
bereits über iperf3-UDP-Tests abgedeckt.

## Klassifikation

Die Ursache wird automatisch anhand der betroffenen Zielgegenstellen eingeordnet
(IPs werden als LAN eingestuft, wenn sie zu einem privaten Adressbereich gehören –
RFC 1918, Loopback, Link-Local):

| Klassifikation | Bedeutung | Empfehlung |
|----------------|-----------|------------|
| `OK` | keine auffälligen Retransmissions | – |
| `LOCAL_NETWORK_ISSUE` | nur Ziele im LAN betroffen | Switch-Ports, Kabel/WLAN, LAN-Auslastung prüfen |
| `REMOTE_SERVER_ISSUE` | genau ein entferntes Ziel betroffen | Serverseitig bzw. beim Betreiber des Ziels nachfragen |
| `UPLINK_CONGESTION` | mehrere WAN-Ziele, nur Upload-Richtung | Upload-Bandbreite/Überlastung prüfen |
| `DOWNLINK_CONGESTION` | mehrere WAN-Ziele, nur Download-Richtung | Download-Bandbreite/Überlastung prüfen |
| `ISP_OR_WAN_LINK_ISSUE` | mehrere unabhängige WAN-Ziele, keine klare Richtung | Providertest/-Nachweis, Leitung prüfen |
| `UNKNOWN` | sowohl LAN- als auch WAN-Ziele betroffen | zunächst LAN prüfen, danach erneut testen |

Die Richtungszuordnung (Upload/Download) setzt voraus, dass netprobe die
IPv4-Adresse des gewählten Interfaces ermitteln kann (`ip -4 addr show`). Gelingt
das nicht, werden Ziele als `direction: unknown` geführt und fließen nicht in die
Up-/Downlink-Klassifikation ein.

## Bedienung

**UI:** Seite *Retransmissions* – „Test starten" (Interface, Dauer, Modus
Passiv/Aktiv; im Modus Passiv optionaler BPF-Filter, Standard `tcp`; im Modus
Aktiv Ziel-Liste mit Quick-Presets, gespeicherten Presets und Verwaltung dieser
Presets), Historie (mit Modus-Spalte), Detailansicht mit Zeitreihe und Liste
der (betroffenen bzw. im Aktiv-Modus aller konfigurierten) Ziele,
Korrelationshinweis (Abgleich mit Dauerping/WAN/Speedtest im Testfenster),
Report-Export.

**API/CLI:** siehe [`docs/operations.md`](operations.md#10-tcp-retransmission-monitoring).

## Report für Provider-Nachweis

Ein Report wird über das bestehende Report-System erzeugt (gleiche
HTML-/PDF-Pipeline wie bei Capture-/Job-Reports, optional PDF via WeasyPrint):

```bash
# UI: Button "Report exportieren" auf der Detailansicht
# CLI:
python -m app.cli retrans-report --test-id 5 --output provider-report.html
python -m app.cli retrans-report --test-id 5 --output provider-report.pdf
```

Der Report enthält Zusammenfassung (Rate, Klassifikation), eine Tabelle der
betroffenen Ziele (LAN/WAN, Richtung, Rate) und die automatische Empfehlung.

## Grenzen

- Aktive Tests unterstützen ausschließlich TCP, maximal 10 Ziele je Test.
- Alarmierung ist lokal (Log + Audit-Log) – es gibt bewusst keine Anbindung an
  externe Dienste (Slack/Teams/E-Mail), um dem „keine Cloud, keine Telemetrie"-
  Grundsatz von netprobe treu zu bleiben.
- Geplantes periodisches Monitoring (Scheduler) läuft weiterhin **passiv** auf
  **einem** konfigurierten Interface (`NETPROBE_RETRANS_DEFAULT_INTERFACE`, Standard
  `auto` = Interface der Default-Route; ein fehlendes/inaktives Interface fällt
  ebenfalls darauf zurück),
  nicht pro Ziel wie bei WAN-Tests. Aktive Tests mit konfigurierten Zielen sind
  aktuell nur manuell (UI/CLI) startbar, nicht als Scheduler-Job.
