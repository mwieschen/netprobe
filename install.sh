#!/usr/bin/env bash
#
# netprobe Installationsscript für Ubuntu Server 22.04 / 24.04 / 26.04.
# Idempotent: erneuter Aufruf zerstört weder Datenbank noch Konfiguration.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
APP_USER="netprobe"
APP_GROUP="netprobe"
APP_DIR="/opt/netprobe"
DATA_DIR="/var/lib/netprobe"
CAPTURE_DIR="${DATA_DIR}/captures"
LOG_DIR="/var/log/netprobe"
ETC_DIR="/etc/netprobe"
ENV_FILE="${ETC_DIR}/netprobe.env"
VENV_DIR="${APP_DIR}/venv"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

C_OK="\033[0;32m"; C_WARN="\033[0;33m"; C_ERR="\033[0;31m"; C_INFO="\033[0;36m"; C_RST="\033[0m"
log()  { echo -e "${C_INFO}[netprobe]${C_RST} $*"; }
ok()   { echo -e "${C_OK}[  ok  ]${C_RST} $*"; }
warn() { echo -e "${C_WARN}[ warn ]${C_RST} $*"; }
die()  { echo -e "${C_ERR}[ fehler ]${C_RST} $*" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

# Wird in detect_os() gesetzt: Codename für Drittanbieter-Repos (Ookla, NodeSource).
# Für sehr neue Ubuntu-Versionen, die dort noch nicht geführt werden, wird auf die
# jeweils letzte unterstützte LTS zurückgefallen.
UBUNTU_VERSION_ID=""
UBUNTU_CODENAME=""
REPO_CODENAME=""

# ---------------------------------------------------------------------------
# 0. Betriebssystem erkennen und Repo-Codename bestimmen
# ---------------------------------------------------------------------------
detect_os() {
    if [ ! -r /etc/os-release ]; then die "Kann /etc/os-release nicht lesen."; fi
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ]; then die "Nur Ubuntu wird unterstützt (gefunden: ${ID:-unbekannt})."; fi
    UBUNTU_VERSION_ID="${VERSION_ID:-}"
    UBUNTU_CODENAME="${VERSION_CODENAME:-}"

    case "$UBUNTU_VERSION_ID" in
        22.04)
            REPO_CODENAME="${UBUNTU_CODENAME:-jammy}"
            ok "Ubuntu ${UBUNTU_VERSION_ID} (${REPO_CODENAME}) erkannt." ;;
        24.04)
            REPO_CODENAME="${UBUNTU_CODENAME:-noble}"
            ok "Ubuntu ${UBUNTU_VERSION_ID} (${REPO_CODENAME}) erkannt." ;;
        26.04)
            # 26.04 wird von Ookla/NodeSource ggf. noch nicht geführt -> 24.04 LTS als Repo-Fallback.
            REPO_CODENAME="noble"
            ok "Ubuntu ${UBUNTU_VERSION_ID} (${UBUNTU_CODENAME:-unbekannt}) erkannt."
            warn "Für Drittanbieter-Repos wird der 24.04-Fallback ('${REPO_CODENAME}') verwendet, falls 26.04 dort fehlt." ;;
        *)
            REPO_CODENAME="noble"
            warn "Ubuntu ${UBUNTU_VERSION_ID:-?} ist ungetestet (unterstützt: 22.04, 24.04, 26.04)."
            warn "Nutze Repo-Fallback '${REPO_CODENAME}'; die Installation wird dennoch versucht." ;;
    esac
}

# ---------------------------------------------------------------------------
# 1. Vorbedingungen prüfen
# ---------------------------------------------------------------------------
check_prereqs() {
    log "Prüfe Vorbedingungen…"

    [ "$(id -u)" -eq 0 ] || die "Bitte als root ausführen (sudo ./install.sh)."
    detect_os

    log "Prüfe Internetverbindung…"
    if ! curl -fsS --max-time 10 https://deb.debian.org >/dev/null 2>&1 && \
       ! curl -fsS --max-time 10 https://archive.ubuntu.com >/dev/null 2>&1; then
        die "Keine Internetverbindung. Für die Installation werden Pakete benötigt."
    fi
    ok "Internetverbindung vorhanden."

    # Freier Speicher (mind. 2 GB auf / )
    local free_mb
    free_mb=$(df -Pm / | awk 'NR==2 {print $4}')
    if [ "${free_mb:-0}" -lt 2048 ]; then
        die "Zu wenig freier Speicher auf / (${free_mb} MB, benötigt: 2048 MB)."
    fi
    ok "Freier Speicher: ${free_mb} MB."

    # Port-80-Konflikt (außer durch nginx selbst)
    if ss -ltnp 2>/dev/null | grep -q ':80 '; then
        if ! ss -ltnp 2>/dev/null | grep ':80 ' | grep -q 'nginx'; then
            warn "Port 80 ist bereits durch einen anderen Dienst belegt. nginx kann nicht starten."
            warn "Bitte den Konflikt auflösen oder den anderen Dienst stoppen."
        fi
    fi
}

# ---------------------------------------------------------------------------
# 2. Pakete installieren
# ---------------------------------------------------------------------------
install_packages() {
    log "Aktualisiere Paketquellen…"
    apt-get update -qq

    log "Installiere Systempakete…"
    # wireshark-common vorab so konfigurieren, dass dumpcap durch Nicht-root
    # nutzbar ist (setuid für Nutzer der Gruppe 'wireshark').
    echo "wireshark-common wireshark-common/install-setuid boolean true" | debconf-set-selections

    apt-get install -y -qq \
        python3 python3-venv python3-dev \
        build-essential \
        nginx \
        sqlite3 \
        tshark wireshark-common \
        fping iputils-ping mtr-tiny \
        curl jq tar ca-certificates gnupg lsb-release ssl-cert

    ok "Systempakete installiert."
    ensure_python
    install_nodejs
    install_speedtest
}

# Stellt sicher, dass Python + venv nutzbar sind (auch mit Python 3.14 auf 26.04).
# Der Dienst läuft später aus einem venv, daher genügt der Standard-python3.
ensure_python() {
    local pyver
    pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
    log "System-Python: ${pyver}"
    # venv-Funktionsfähigkeit prüfen; bei Bedarf versioniertes Paket nachinstallieren
    if ! python3 -m venv --help >/dev/null 2>&1; then
        warn "python3-venv scheint unvollständig; versuche python${pyver}-venv…"
        apt-get install -y -qq "python${pyver}-venv" "python${pyver}-dev" 2>/dev/null || true
    fi
    if ! python3 -m venv --help >/dev/null 2>&1; then
        die "Python-venv ist nicht verfügbar. Bitte 'python3-venv' manuell installieren."
    fi
    # Python 3.12+ ist Voraussetzung (Backend nutzt 3.12-Syntax; 3.14 wird unterstützt).
    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)'; then
        die "Python ${pyver} ist zu alt. Benötigt wird mindestens Python 3.12."
    fi
    ok "Python ${pyver} mit venv verfügbar."
}

_node_major() { command -v node >/dev/null 2>&1 && node -v | sed 's/v\([0-9]*\).*/\1/' || echo 0; }

install_nodejs() {
    if [ "$(_node_major)" -ge 20 ]; then
        ok "Node.js $(node -v) bereits vorhanden."
        return
    fi

    # 1) Bevorzugt Node.js 24 von NodeSource. Das Setup-Skript erkennt den Codename
    #    selbst; auf sehr neuen Ubuntu-Versionen kann das fehlschlagen -> Fallback.
    log "Installiere Node.js 24 (NodeSource)…"
    if curl -fsSL https://deb.nodesource.com/setup_24.x | bash - >/dev/null 2>&1; then
        apt-get install -y -qq nodejs || true
    fi

    # 2) Fallback: Node.js aus den Ubuntu-Paketquellen (auf 26.04 aktuell genug).
    if [ "$(_node_major)" -lt 20 ]; then
        warn "NodeSource fehlgeschlagen oder Version zu alt; nutze Ubuntu-Paketquellen…"
        apt-get install -y -qq nodejs npm || true
    fi

    if [ "$(_node_major)" -ge 20 ]; then
        ok "Node.js $(node -v) installiert."
    else
        die "Node.js (>=20) konnte nicht installiert werden. Bitte manuell installieren (siehe README)."
    fi
}

# Version der offiziellen Ookla Speedtest CLI für den Tarball-Fallback.
OOKLA_CLI_VERSION="1.2.0"

install_speedtest() {
    if command -v speedtest >/dev/null 2>&1; then
        ok "Ookla Speedtest CLI bereits vorhanden."
        return
    fi
    log "Installiere offizielle Ookla Speedtest CLI…"

    # 1) APT-Repository (packagecloud). Der Codename wird auf REPO_CODENAME
    #    gesetzt, damit auf 26.04 der 24.04-Fallback ('noble') genutzt wird,
    #    solange Ookla 26.04 nicht führt. Das Skript honoriert os/dist.
    if curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh \
        | os=ubuntu dist="${REPO_CODENAME}" bash - >/dev/null 2>&1; then
        apt-get install -y -qq speedtest >/dev/null 2>&1 || true
    fi

    # 2) Fallback: offizielles Tarball direkt von Ookla installieren.
    if ! command -v speedtest >/dev/null 2>&1; then
        warn "APT-Installation der Speedtest CLI fehlgeschlagen; versuche offizielles Tarball…"
        install_speedtest_tarball || true
    fi

    if command -v speedtest >/dev/null 2>&1; then
        ok "Speedtest CLI installiert ($(speedtest --version 2>/dev/null | head -n1 || echo ok))."
    else
        warn "Speedtest CLI konnte NICHT installiert werden. Speedtests bleiben deaktiviert,"
        warn "bis die CLI vorhanden ist (siehe README, Abschnitt 'Speedtest CLI manuell')."
    fi
}

# Lädt das offizielle Ookla-Tarball und legt die Binary nach /usr/local/bin.
install_speedtest_tarball() {
    local arch url tmp
    case "$(uname -m)" in
        x86_64)        arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        armv7l|armhf)  arch="armhf" ;;
        i386|i686)     arch="i386" ;;
        *) warn "Speedtest-Tarball: Architektur $(uname -m) nicht unterstützt."; return 1 ;;
    esac
    url="https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_CLI_VERSION}-linux-${arch}.tgz"
    tmp="$(mktemp -d)"
    if curl -fsSL "$url" -o "$tmp/speedtest.tgz"; then
        if tar -xzf "$tmp/speedtest.tgz" -C "$tmp" 2>/dev/null && [ -f "$tmp/speedtest" ]; then
            install -m 0755 "$tmp/speedtest" /usr/local/bin/speedtest
            rm -rf "$tmp"
            return 0
        fi
    fi
    warn "Tarball-Download/-Entpacken fehlgeschlagen: $url"
    rm -rf "$tmp"
    return 1
}

# ---------------------------------------------------------------------------
# 3. Benutzer, Verzeichnisse, Capabilities
# ---------------------------------------------------------------------------
setup_user_dirs() {
    if ! id "$APP_USER" >/dev/null 2>&1; then
        log "Erstelle Systembenutzer '${APP_USER}'…"
        useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
    fi
    # Für dumpcap-Zugriff der Gruppe 'wireshark' hinzufügen (falls vorhanden)
    if getent group wireshark >/dev/null 2>&1; then
        usermod -aG wireshark "$APP_USER"
        ok "Benutzer '${APP_USER}' der Gruppe 'wireshark' hinzugefügt."
    fi

    mkdir -p "$APP_DIR" "$DATA_DIR" "$CAPTURE_DIR" "$LOG_DIR" "$ETC_DIR"
    chown -R "$APP_USER:$APP_GROUP" "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
    chmod 750 "$DATA_DIR" "$CAPTURE_DIR" "$LOG_DIR"
    ok "Verzeichnisse angelegt."

    # Sicherstellen, dass dumpcap die nötigen Capabilities besitzt
    if [ -x /usr/bin/dumpcap ]; then
        setcap 'cap_net_raw,cap_net_admin=eip' /usr/bin/dumpcap 2>/dev/null || \
            warn "setcap auf dumpcap fehlgeschlagen; Packet Capture ggf. eingeschränkt."
    fi
}

# ---------------------------------------------------------------------------
# 4. Anwendung ausrollen
# ---------------------------------------------------------------------------
deploy_app() {
    log "Kopiere Anwendungsdateien nach ${APP_DIR}…"
    mkdir -p "$APP_DIR/backend" "$APP_DIR/frontend"
    # rsync bewahrt keine venv/dist; wir kopieren Quellcode selektiv.
    cp -r "$SRC_DIR/backend/app" "$APP_DIR/backend/"
    cp -r "$SRC_DIR/backend/alembic" "$APP_DIR/backend/"
    cp "$SRC_DIR/backend/alembic.ini" "$APP_DIR/backend/"
    cp "$SRC_DIR/backend/pyproject.toml" "$APP_DIR/backend/"

    log "Erstelle virtuelle Python-Umgebung…"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet "$APP_DIR/backend"
    ok "Python-Abhängigkeiten installiert."

    build_frontend
    chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}

build_frontend() {
    log "Baue Frontend (Vite)…"
    local build_dir
    build_dir="$(mktemp -d)"
    cp -r "$SRC_DIR/frontend/." "$build_dir/"
    ( cd "$build_dir" && npm ci --silent 2>/dev/null || npm install --silent )
    ( cd "$build_dir" && npm run build --silent )
    rm -rf "$APP_DIR/frontend/dist"
    mkdir -p "$APP_DIR/frontend"
    cp -r "$build_dir/dist" "$APP_DIR/frontend/dist"
    rm -rf "$build_dir"
    ok "Frontend gebaut."
}

# ---------------------------------------------------------------------------
# 5. Konfiguration (idempotent)
# ---------------------------------------------------------------------------
setup_env() {
    if [ -f "$ENV_FILE" ]; then
        ok "Konfiguration ${ENV_FILE} existiert bereits – bleibt unverändert."
        return
    fi
    log "Erstelle Konfiguration ${ENV_FILE}…"
    local secret
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    cp "$SRC_DIR/.env.example" "$ENV_FILE"
    sed -i "s|^NETPROBE_SECRET_KEY=.*|NETPROBE_SECRET_KEY=${secret}|" "$ENV_FILE"
    chown root:"$APP_GROUP" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    ok "Konfiguration erstellt (zufälliger SECRET_KEY gesetzt)."
}

run_migrations() {
    log "Führe Datenbankmigrationen aus…"
    ( cd "$APP_DIR/backend" && \
      sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" "$VENV_DIR/bin/alembic" upgrade head )
    ok "Datenbank ist auf dem aktuellen Stand."
}

setup_admin() {
    # Existiert bereits ein Benutzer? Dann nichts tun (idempotent).
    local count
    count="$(cd "$APP_DIR/backend" && sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" \
        "$VENV_DIR/bin/python" - <<'PY' 2>/dev/null || echo 0
from app.db.session import SessionLocal
from app.models.user import User
db = SessionLocal()
print(db.query(User).count())
db.close()
PY
)"
    if [ "${count:-0}" -gt 0 ]; then
        ok "Es existiert bereits mindestens ein Benutzer – Admin-Anlage übersprungen."
        return
    fi

    echo
    log "Initialen Admin-Benutzer anlegen."
    local admin_user admin_pass admin_pass2
    read -r -p "Admin-Benutzername [admin]: " admin_user
    admin_user="${admin_user:-admin}"
    while true; do
        read -r -s -p "Admin-Passwort (min. 8 Zeichen): " admin_pass; echo
        read -r -s -p "Passwort bestätigen: " admin_pass2; echo
        if [ "$admin_pass" != "$admin_pass2" ]; then warn "Passwörter stimmen nicht überein."; continue; fi
        if [ "${#admin_pass}" -lt 8 ]; then warn "Passwort zu kurz."; continue; fi
        break
    done

    # Passwort über Umgebungsvariable an CLI übergeben (nicht als Argument -> nicht in ps sichtbar);
    # das Passwort wird nirgends im Klartext geloggt.
    ( cd "$APP_DIR/backend" && \
      sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" \
        NETPROBE_ADMIN_USER="$admin_user" NETPROBE_ADMIN_PASSWORD="$admin_pass" \
        "$VENV_DIR/bin/python" -m app.cli ensure-admin >/dev/null )
    unset admin_pass admin_pass2
    ok "Admin-Benutzer '${admin_user}' angelegt."
}

# ---------------------------------------------------------------------------
# 6. systemd, nginx, logrotate
# ---------------------------------------------------------------------------
setup_systemd() {
    log "Installiere systemd-Service…"
    cp "$SRC_DIR/deploy/systemd/netprobe.service" /etc/systemd/system/netprobe.service
    systemctl daemon-reload
    systemctl enable netprobe >/dev/null 2>&1
    systemctl restart netprobe
    ok "Dienst netprobe aktiviert und gestartet."
}

setup_nginx() {
    log "Konfiguriere nginx…"
    cp "$SRC_DIR/deploy/nginx/netprobe.conf" /etc/nginx/sites-available/netprobe.conf
    ln -sf /etc/nginx/sites-available/netprobe.conf /etc/nginx/sites-enabled/netprobe.conf
    rm -f /etc/nginx/sites-enabled/default
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        ok "nginx konfiguriert."
    else
        warn "nginx-Konfigurationstest fehlgeschlagen; bitte 'nginx -t' prüfen."
    fi
}

setup_logrotate() {
    cp "$SRC_DIR/deploy/logrotate/netprobe" /etc/logrotate.d/netprobe
    ok "logrotate konfiguriert."
}

setup_ufw() {
    command -v ufw >/dev/null 2>&1 || return 0
    read -r -p "UFW-Firewallregel für HTTP (Port 80) hinzufügen? [j/N]: " ans
    if [[ "${ans,,}" == "j" ]]; then
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        ok "UFW-Regeln für Port 80/443 hinzugefügt (UFW ggf. noch aktivieren)."
    fi
}

# ---------------------------------------------------------------------------
# 7. Netzwerk-Konfiguration (optional, mit netplan try)
# ---------------------------------------------------------------------------
configure_network() {
    echo
    log "Netzwerk-Konfiguration"
    echo "  1) Bestehendes Netzwerk unverändert lassen (empfohlen)"
    echo "  2) DHCP konfigurieren"
    echo "  3) Statische IPv4 konfigurieren"
    read -r -p "Auswahl [1]: " net_choice
    net_choice="${net_choice:-1}"

    case "$net_choice" in
        1) ok "Netzwerk bleibt unverändert."; return ;;
        2) network_dhcp ;;
        3) network_static ;;
        *) warn "Ungültige Auswahl – Netzwerk bleibt unverändert."; return ;;
    esac
}

backup_netplan() {
    local ts; ts="$(date +%Y%m%d_%H%M%S)"
    mkdir -p "/etc/netplan/backup_${ts}"
    cp -a /etc/netplan/*.yaml "/etc/netplan/backup_${ts}/" 2>/dev/null || true
    ok "Vorhandene Netplan-Dateien gesichert nach /etc/netplan/backup_${ts}/"
}

warn_connectivity() {
    echo
    warn "ACHTUNG: Änderungen an der Netzwerkkonfiguration können die Verbindung unterbrechen."
    warn "Es wird ausschließlich 'netplan try' verwendet (automatischer Rollback nach 120 s)."
    warn "Eine LOKALE KONSOLE (Bildschirm/Tastatur oder KVM) wird dringend empfohlen."
    read -r -p "Fortfahren? [j/N]: " c
    [[ "${c,,}" == "j" ]] || { ok "Netzwerkänderung abgebrochen."; return 1; }
    return 0
}

network_dhcp() {
    warn_connectivity || return 0
    read -r -p "Interface (z. B. eth0): " iface
    [ -n "$iface" ] || { warn "Kein Interface angegeben."; return 0; }
    backup_netplan
    cat > /etc/netplan/99-netprobe.yaml <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    ${iface}:
      dhcp4: true
EOF
    chmod 600 /etc/netplan/99-netprobe.yaml
    log "Wende Konfiguration mit 'netplan try' an…"
    netplan try || warn "netplan try beendet (ggf. Rollback). Bitte Konfiguration prüfen."
}

network_static() {
    warn_connectivity || return 0
    read -r -p "Interface (z. B. eth0): " iface
    read -r -p "IPv4-Adresse (z. B. 192.168.1.50): " ipaddr
    read -r -p "Präfix (z. B. 24): " prefix
    read -r -p "Gateway (z. B. 192.168.1.1): " gw
    read -r -p "DNS-Server (kommagetrennt, z. B. 1.1.1.1,8.8.8.8): " dns
    if [ -z "$iface" ] || [ -z "$ipaddr" ] || [ -z "$prefix" ] || [ -z "$gw" ]; then
        warn "Unvollständige Angaben – Netzwerkänderung abgebrochen."
        return 0
    fi
    local dns_yaml=""
    IFS=',' read -ra dns_arr <<< "$dns"
    for d in "${dns_arr[@]}"; do dns_yaml+="          - ${d// /}\n"; done
    backup_netplan
    cat > /etc/netplan/99-netprobe.yaml <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    ${iface}:
      dhcp4: false
      addresses:
        - ${ipaddr}/${prefix}
      routes:
        - to: default
          via: ${gw}
      nameservers:
        addresses:
$(echo -e "$dns_yaml")
EOF
    chmod 600 /etc/netplan/99-netprobe.yaml
    log "Wende Konfiguration mit 'netplan try' an…"
    netplan try || warn "netplan try beendet (ggf. Rollback). Bitte Konfiguration prüfen."
}

# ---------------------------------------------------------------------------
# 8. Smoke-Tests
# ---------------------------------------------------------------------------
smoke_tests() {
    echo
    log "Führe Smoke-Tests durch…"
    sleep 3

    if systemctl is-active --quiet netprobe; then
        ok "systemd-Dienst läuft."
    else
        warn "Dienst läuft nicht. Details: journalctl -u netprobe -n 50"
    fi

    if curl -fsS --max-time 10 http://127.0.0.1:8000/api/health | grep -q '"ok"'; then
        ok "Backend-Healthcheck (127.0.0.1:8000) erfolgreich."
    else
        warn "Backend-Healthcheck fehlgeschlagen."
    fi

    if curl -fsS --max-time 10 http://127.0.0.1/api/health | grep -q '"ok"'; then
        ok "nginx-Proxy-Healthcheck (Port 80) erfolgreich."
    else
        warn "nginx-Proxy-Healthcheck fehlgeschlagen."
    fi
}

print_summary() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo
    echo -e "${C_OK}==============================================================${C_RST}"
    echo -e "${C_OK} netprobe wurde installiert.${C_RST}"
    echo -e "${C_OK}==============================================================${C_RST}"
    echo "  Weboberfläche:  http://${ip:-<server-ip>}/"
    echo "  API-Dokumentation: http://${ip:-<server-ip>}/api/docs"
    echo "  Konfiguration:  ${ENV_FILE}"
    echo "  Dienststatus:   systemctl status netprobe"
    echo "  Logs:           journalctl -u netprobe -f"
    echo
    echo "  TLS aktivieren: siehe deploy/nginx/netprobe.conf und docs/security.md"
    echo
}

# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------
main() {
    check_prereqs
    install_packages
    setup_user_dirs
    deploy_app
    setup_env
    run_migrations
    setup_admin
    setup_systemd
    setup_nginx
    setup_logrotate
    setup_ufw
    configure_network
    smoke_tests
    print_summary
}

main "$@"
