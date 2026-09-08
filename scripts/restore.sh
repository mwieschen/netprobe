#!/usr/bin/env bash
# Stellt die SQLite-Datenbank aus einem Backup wieder her.
# Verwendung: restore.sh <backup.sqlite[.gz]>
set -euo pipefail

ENV_FILE="${NETPROBE_ENV_FILE:-/etc/netprobe/netprobe.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

DATA_DIR="${NETPROBE_DATA_DIR:-/var/lib/netprobe}"
DB="${DATA_DIR}/db.sqlite"
SRC="${1:?Bitte Backup-Datei angeben}"

if [ ! -f "$SRC" ]; then
    echo "Backup nicht gefunden: $SRC" >&2
    exit 1
fi

echo "Stoppe netprobe-Dienst…"
systemctl stop netprobe || true

TMP="$(mktemp)"
if [[ "$SRC" == *.gz ]]; then
    gunzip -c "$SRC" > "$TMP"
else
    cp "$SRC" "$TMP"
fi

# Integritätsprüfung vor dem Überschreiben
if ! sqlite3 "$TMP" "PRAGMA integrity_check;" | grep -q "ok"; then
    echo "Integritätsprüfung fehlgeschlagen – Abbruch." >&2
    rm -f "$TMP"
    exit 1
fi

# Bestehende DB sichern
[ -f "$DB" ] && cp "$DB" "${DB}.pre-restore.$(date +%s)"
mv "$TMP" "$DB"
chown netprobe:netprobe "$DB"

echo "Starte netprobe-Dienst…"
systemctl start netprobe
echo "Wiederherstellung abgeschlossen."
