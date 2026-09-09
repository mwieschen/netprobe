#!/usr/bin/env bash
#
# netprobe – Berechtigungen & Capabilities reparieren.
#
# Behebt (idempotent) die drei häufigsten Betriebsprobleme, ohne einen kompletten
# Neulauf von install.sh:
#   1) "Permission denied" beim Schreiben von PCAP-Dateien in /var/lib/netprobe/captures
#   2) Dauerping/WAN-Ping liefert nur Timeouts (ping/fping ohne CAP_NET_RAW)
#   3) Speedtest schlägt fehl (Ookla-CLI ohne beschreibbares HOME / Lizenz)
#
# Mehrfaches Ausführen ist unschädlich.
#
#   sudo ./scripts/fix-permissions.sh
#
set -euo pipefail

APP_USER="netprobe"
APP_GROUP="netprobe"
APP_DIR="/opt/netprobe"
DATA_DIR="/var/lib/netprobe"
CAPTURE_DIR="${DATA_DIR}/captures"
LOG_DIR="/var/log/netprobe"
UNIT_SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

C_OK="\033[0;32m"; C_WARN="\033[0;33m"; C_INFO="\033[0;36m"; C_RST="\033[0m"
log()  { echo -e "${C_INFO}[fix]${C_RST} $*"; }
ok()   { echo -e "${C_OK}[ ok ]${C_RST} $*"; }
warn() { echo -e "${C_WARN}[warn]${C_RST} $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { echo "Benutzer '$APP_USER' fehlt – bitte zuerst install.sh ausführen." >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Verzeichnisse & Eigentümer
# ---------------------------------------------------------------------------
log "Richte Verzeichnisse und Berechtigungen ein…"
mkdir -p "$DATA_DIR" "$CAPTURE_DIR" "$LOG_DIR" \
         "$DATA_DIR/.config/ookla" "$DATA_DIR/.cache"

chown "$APP_USER:$APP_GROUP" "$DATA_DIR" "$LOG_DIR"
chmod 750 "$DATA_DIR" "$LOG_DIR"
chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR/.config" "$DATA_DIR/.cache"
chmod 700 "$DATA_DIR/.config" "$DATA_DIR/.cache"

# netprobe zur Gruppe 'wireshark' hinzufügen (für manuelle dumpcap/tshark-Nutzung)
if getent group wireshark >/dev/null 2>&1; then
    id -nG "$APP_USER" | tr ' ' '\n' | grep -qx wireshark || usermod -aG wireshark "$APP_USER"
    chown "$APP_USER:wireshark" "$CAPTURE_DIR"
    chmod 2770 "$CAPTURE_DIR"          # setgid: neue PCAPs erben Gruppe 'wireshark'
else
    chown "$APP_USER:$APP_GROUP" "$CAPTURE_DIR"
    chmod 2750 "$CAPTURE_DIR"
fi
# Bereits vorhandene Capture-Dateien auf die Verzeichnis-Gruppe umziehen
find "$CAPTURE_DIR" -maxdepth 1 -type f \( -name '*.pcap' -o -name '*.pcapng' \) \
    -exec chgrp --reference="$CAPTURE_DIR" {} + 2>/dev/null || true
find "$CAPTURE_DIR" -maxdepth 1 -type f -exec chmod g+rw {} + 2>/dev/null || true
ok "Captures: $(stat -c '%U:%G %a' "$CAPTURE_DIR")"

# ---------------------------------------------------------------------------
# 2. File-Capabilities für manuelle Aufrufe (Dienst nutzt AmbientCapabilities)
# ---------------------------------------------------------------------------
log "Setze File-Capabilities auf Mess-Binaries…"
if command -v setcap >/dev/null 2>&1; then
    if D="$(command -v dumpcap)"; then
        setcap 'cap_net_raw,cap_net_admin=eip' "$D" 2>/dev/null && ok "dumpcap: $(getcap "$D")" \
            || warn "setcap dumpcap fehlgeschlagen."
    fi
    for b in fping ping; do
        if P="$(command -v "$b")"; then
            setcap 'cap_net_raw+ep' "$P" 2>/dev/null && ok "$b: $(getcap "$P")" \
                || warn "setcap $b fehlgeschlagen (evtl. Symlink/Multi-Call-Binary)."
        fi
    done
else
    warn "setcap nicht vorhanden – 'sudo apt-get install libcap2-bin'."
fi

# ---------------------------------------------------------------------------
# 3. systemd-Unit aktualisieren (AmbientCapabilities, HOME, PATH)
# ---------------------------------------------------------------------------
UNIT_SRC="${UNIT_SRC_DIR}/deploy/systemd/netprobe.service"
if [ -f "$UNIT_SRC" ]; then
    if ! cmp -s "$UNIT_SRC" /etc/systemd/system/netprobe.service 2>/dev/null; then
        log "Aktualisiere /etc/systemd/system/netprobe.service…"
        cp "$UNIT_SRC" /etc/systemd/system/netprobe.service
        systemctl daemon-reload
        ok "Unit aktualisiert."
    else
        ok "systemd-Unit ist bereits aktuell."
    fi
else
    warn "Unit-Vorlage nicht gefunden: $UNIT_SRC (Skript aus dem Repo ausführen)."
fi

# ---------------------------------------------------------------------------
# 4. Ookla-Lizenz im netprobe-Kontext bestätigen
# ---------------------------------------------------------------------------
if command -v speedtest >/dev/null 2>&1; then
    log "Bestätige Ookla-Lizenz für '${APP_USER}' ($(command -v speedtest))…"
    install -d -o "$APP_USER" -g "$APP_GROUP" -m 700 "$DATA_DIR/.config" "$DATA_DIR/.config/ookla"
    if sudo -u "$APP_USER" env HOME="$DATA_DIR" XDG_CONFIG_HOME="$DATA_DIR/.config" \
        XDG_CACHE_HOME="$DATA_DIR/.cache" \
        speedtest --accept-license --accept-gdpr --progress=no --version >/dev/null 2>&1; then
        ok "Ookla-Lizenz akzeptiert."
    else
        warn "Speedtest-Vorabtest fehlgeschlagen – Netzwerk/Firewall prüfen."
    fi
else
    warn "Speedtest CLI nicht gefunden. Siehe README ('Speedtest CLI manuell')."
fi

# ---------------------------------------------------------------------------
# 5. Dienst neu starten & verifizieren
# ---------------------------------------------------------------------------
log "Starte netprobe-Dienst neu…"
systemctl restart netprobe
sleep 2

if systemctl is-active --quiet netprobe; then
    ok "Dienst läuft."
else
    warn "Dienst läuft nicht – 'journalctl -u netprobe -n 50' prüfen."
fi

eff="$(cat /proc/"$(systemctl show -p MainPID --value netprobe)"/status 2>/dev/null | awk '/CapEff/{print $2}')"
[ -n "${eff:-}" ] && log "CapEff des Dienstes: 0x${eff}  (CAP_NET_RAW=Bit 13, CAP_NET_ADMIN=Bit 12; 0x3000 = beide)"

echo
ok "Fertig. Test:  ping/WAN im Web-UI starten, Speedtest auslösen, Packet Capture starten."
