#!/usr/bin/env bash
# Sicheres Backup der SQLite-Datenbank via 'sqlite3 .backup' (konsistent bei WAL).
set -euo pipefail

ENV_FILE="${NETPROBE_ENV_FILE:-/etc/netprobe/netprobe.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

DATA_DIR="${NETPROBE_DATA_DIR:-/var/lib/netprobe}"
DB="${DATA_DIR}/db.sqlite"
BACKUP_DIR="${1:-${DATA_DIR}/backups}"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="${BACKUP_DIR}/netprobe_${TS}.sqlite"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB" ]; then
    echo "Datenbank nicht gefunden: $DB" >&2
    exit 1
fi

# .backup ist gegenüber einfachem Kopieren sicher, auch bei laufendem Dienst.
sqlite3 "$DB" ".backup '${OUT}'"
gzip -f "$OUT"
echo "Backup erstellt: ${OUT}.gz"

# Alte Backups (>30 Tage) aufräumen
find "$BACKUP_DIR" -name 'netprobe_*.sqlite.gz' -mtime +30 -delete 2>/dev/null || true
