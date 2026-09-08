# netprobe – Betrieb: Update, Backup, Restore, Fehleranalyse, Deinstallation

## Update

netprobe wird durch erneutes Ausrollen des Quellcodes aktualisiert. Das
Installationsscript ist idempotent und zerstört weder Datenbank noch Konfiguration.

```bash
# Im entpackten/aktualisierten Repository:
sudo ./install.sh
```

Alternativ manuell:

```bash
sudo systemctl stop netprobe
sudo cp -r backend/app /opt/netprobe/backend/
sudo cp -r backend/alembic /opt/netprobe/backend/
sudo /opt/netprobe/venv/bin/pip install /opt/netprobe/backend
# Frontend neu bauen und nach /opt/netprobe/frontend/dist kopieren
sudo -u netprobe env NETPROBE_ENV_FILE=/etc/netprobe/netprobe.env \
    /opt/netprobe/venv/bin/alembic -c /opt/netprobe/backend/alembic.ini upgrade head
sudo systemctl start netprobe
```

Migrationen werden über Alembic eingespielt (`alembic upgrade head`).

## Backup

Die SQLite-Datenbank wird konsistent mit `sqlite3 .backup` gesichert (WAL-sicher):

```bash
sudo /opt/netprobe/backend/../scripts/backup.sh          # Standardziel: /var/lib/netprobe/backups
# oder mit Repo-Skript:
sudo ./scripts/backup.sh /pfad/zum/backupordner
```

Das Skript erzeugt eine komprimierte Kopie `netprobe_<zeitstempel>.sqlite.gz` und
räumt Backups älter als 30 Tage auf.

**Automatisieren** (z. B. tägliches Backup um 03:30 Uhr) via cron:

```bash
echo '30 3 * * * root /opt/netprobe/scripts/backup.sh' | sudo tee /etc/cron.d/netprobe-backup
```

Alternativ innerhalb von SQLite: `VACUUM INTO '/pfad/backup.sqlite';`

## Restore

```bash
sudo ./scripts/restore.sh /var/lib/netprobe/backups/netprobe_20260101_033000.sqlite.gz
```

Das Skript stoppt den Dienst, prüft die Integrität (`PRAGMA integrity_check`),
sichert die bestehende DB und spielt das Backup ein, danach Neustart.

## Fehleranalyse

| Symptom                                   | Vorgehen                                                        |
|-------------------------------------------|----------------------------------------------------------------|
| Weboberfläche nicht erreichbar            | `systemctl status netprobe` und `systemctl status nginx`       |
| Dienst startet nicht                      | `journalctl -u netprobe -n 100 --no-pager`                     |
| 502 Bad Gateway von nginx                 | Backend prüfen: `curl http://127.0.0.1:8000/api/health`        |
| Speedtest schlägt fehl                    | `which speedtest`; manuell testen: `speedtest --accept-license`|
| Packet Capture „dumpcap fehlt/keine Rechte“ | `getcap /usr/bin/dumpcap`; Benutzer in Gruppe `wireshark`?   |
| Keine WAN-Messwerte                        | `which fping`; Ziel erreichbar? `fping -C5 <host>`             |
| Dauerping ohne Daten                       | Job-Status „running“? Ping-Tool vorhanden? Logs prüfen.        |
| SSE/Live-Ansicht aktualisiert nicht        | nginx-Buffering für `/api/ping/stream` deaktiviert? (Config)   |

Healthcheck-Endpunkt: `curl http://<server>/api/health` → `{"status":"ok"}`.

Werkzeug-Verfügbarkeit wird im Dashboard unter *Werkzeuge* angezeigt.

### Logs

- Dienst/Anwendung: `journalctl -u netprobe -f`
- Datei-Log: `/var/log/netprobe/netprobe.log` (rotiert via logrotate)
- nginx: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`

## Deinstallation

```bash
sudo systemctl disable --now netprobe
sudo rm -f /etc/systemd/system/netprobe.service
sudo systemctl daemon-reload

sudo rm -f /etc/nginx/sites-enabled/netprobe.conf /etc/nginx/sites-available/netprobe.conf
sudo systemctl reload nginx

sudo rm -f /etc/logrotate.d/netprobe
sudo rm -rf /opt/netprobe

# Daten & Konfiguration – nur entfernen, wenn wirklich gewünscht (Backup vorher!):
sudo rm -rf /var/lib/netprobe /var/log/netprobe /etc/netprobe

# Systembenutzer entfernen:
sudo userdel netprobe 2>/dev/null || true
```

Netplan-Änderungen (falls während der Installation vorgenommen) liegen in
`/etc/netplan/99-netprobe.yaml`; Sicherungskopien unter `/etc/netplan/backup_*`.
