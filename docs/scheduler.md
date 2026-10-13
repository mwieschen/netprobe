# Scheduler: globale Automatisierung

netprobe führt automatisierte Tests über eine **einzige** In-Prozess-APScheduler-
Instanz aus (`app/workers/scheduler.py`, läuft im selben Prozess wie die API).
Die Seite *Scheduler* gibt einen Überblick über alle automatisch geplanten Tests
und einen globalen Pause/Aktivieren-Schalter.

## Was ist "ein Test" im Scheduler-Sinn?

| Test | Konfiguration | Wo einstellbar |
|------|---------------|----------------|
| Speedtest | ein globales Intervall | *Einstellungen* |
| TCP-Retransmission-Monitoring | ein globales Intervall, ein Interface | *Einstellungen* |
| WAN-Tests | **pro Ziel** ein Intervall | *WAN-Tests* |
| VoIP/3CX | **pro Server** ein Intervall | *VoIP / 3CX* |
| Traceroute | **pro Ziel** ein Intervall | *Traceroute* |
| System-Health-Checks | **pro Probe** ein Intervall | *System Health* |

Dauerping (`/ping`) ist **kein** Scheduler-Job: Ein laufender Ping-Job ist ein
dauerhafter Hintergrund-Task (eine Zielverbindung, die kontinuierlich pingt),
kein periodisch wiederholter Test, und taucht deshalb nicht in der
Scheduler-Übersicht auf.

Die Seite *Scheduler* ist bewusst eine **reine Übersicht**: Intervalle für
WAN/VoIP/Traceroute/Health werden weiterhin auf der jeweiligen Feature-Seite
bearbeitet (dort existiert pro Ziel bereits ein Intervall-Feld samt
Live-Resync des Schedulers bei jeder Änderung). Eine zweite Konfigurationsstelle
für dieselben Werte würde nur zu Verwirrung führen, welche gerade gilt.

## Globaler Automatisierungs-Schalter

`POST /api/scheduler/enable` / `POST /api/scheduler/disable` (admin) pausiert
bzw. aktiviert **alle** Test-Jobs sofort, ohne Neustart und ohne die
Scheduler-Jobs zu entfernen (APScheduler `pause()`/`resume()`). Housekeeping
(5-Minuten-Ping-Aggregation, Retention-Cleanup) läuft davon unabhängig immer
weiter – das sind keine "Tests".

Der Schalter wird im KV-Store gespiegelt (`scheduler_enabled`, Startwert
`NETPROBE_SCHEDULER_ENABLED`) und bei jedem Start sowie jeder Neusynchronisation
(z. B. nach Anlegen eines neuen WAN-Ziels) erneut angewendet, damit auch neu
hinzugekommene Jobs im pausierten Zustand starten.

**Manuelle Tests ("Jetzt ausführen") funktionieren immer**, unabhängig vom
globalen Schalter – der Schalter betrifft ausschließlich die automatische
Wiederholung.

## API

| Endpoint | Auth | Zweck |
|----------|------|-------|
| `GET /api/scheduler/status` | viewer | alle geplanten Jobs: Typ, Ziel, aktiviert/pausiert, Intervall, nächster/letzter Lauf, letztes Ergebnis |
| `POST /api/scheduler/enable` | admin | globale Automatisierung aktivieren |
| `POST /api/scheduler/disable` | admin | globale Automatisierung pausieren |

"Letzter Lauf"/"letztes Ergebnis" werden aus der jeweiligen Ergebnistabelle des
Tests gelesen (z. B. `wan_results`, `voip_tests`) – es gibt bewusst **keine**
separate Lauf-Historien-Tabelle, um Daten nicht doppelt zu halten.

## CLI

```bash
python -m app.cli scheduler-status
python -m app.cli scheduler-enable
python -m app.cli scheduler-disable
```

Diese Befehle schreiben direkt in die Datenbank (KV-Store) und laufen in einem
**eigenen** Prozess. Läuft der netprobe-Dienst bereits, wirkt die Änderung erst
nach dessen Neustart – für sofortige Wirkung ohne Neustart die Seite
*Scheduler* bzw. die API desselben laufenden Prozesses verwenden.
