#!/usr/bin/env bash
#
# netprobe – selbstheilender Installer/Updater für Ubuntu Server 22.04 / 24.04 / 26.04.
#
# Ein Befehl für alles (ohne Rückfragen):
#   curl -sSL https://raw.githubusercontent.com/mwieschen/netprobe/main/install.sh | sudo bash
#   sudo bash install.sh                  # aus Release-Paket oder Repository-Checkout
#
# Der Installer erkennt selbst, was zu tun ist:
#   - Keine Installation (/opt/netprobe/venv fehlt) -> Neuinstallation: Pakete, Benutzer,
#     Verzeichnisse, venv, Konfiguration, Migrationen, Admin, systemd, nginx.
#   - Bestehende Installation -> Smart-Update: vergleicht Backend-Code und Frontend (md5),
#     Python-Abhängigkeiten (pyproject.toml), systemd-Unit und Migrationsstand
#     (alembic current vs. heads) und spielt NUR Geändertes ein. Vorher immer ein
#     Snapshot (Code + Konfiguration + DB); schlägt die Migration fehl, wird automatisch
#     auf den Snapshot zurückgerollt. Ist alles aktuell, wird nichts neu gestartet.
#   - Fehlende Teile (Pakete, Verzeichnisse, Konfiguration, Admin, Dienst) werden bei
#     jedem Lauf ergänzt – der Installer ist beliebig oft ausführbar.
#
# Quelle: liegt das Skript in einem netprobe-Verzeichnis (Release-Paket mit RELEASE-Datei
# oder Repository-Checkout), wird dieses verwendet. Per "curl | bash" wird die Quelle von
# GitHub geladen: zuerst das neueste Release-Paket (Asset "netprobe.tar.gz", Frontend
# vorgebaut, kein Node.js nötig), sonst das Quellarchiv des Branches (Frontend wird gebaut).
#   NETPROBE_REPO=<owner>/<repo>   GitHub-Repository        (Default: mwieschen/netprobe)
#   NETPROBE_REF=<branch|tag|sha>  bestimmter Stand statt "neuestes Release"
#   NETPROBE_SOURCE_URL=<url>      beliebiges tar.gz (Release-Paket oder Quellarchiv)
#   NETPROBE_ADMIN_USER / NETPROBE_ADMIN_PASSWORD   initialer Admin (sonst Zufallspasswort)
#   NETPROBE_KEEP_SNAPSHOTS=<n>    aufbewahrte Update-Snapshots (Default: 5)
# Bei "curl | sudo bash" Variablen hinter sudo setzen: ... | sudo NETPROBE_REF=v1.2 bash
#
# Optionen (bei "curl | bash": ... | sudo bash -s -- <optionen>):
#   --rebuild-frontend   Frontend erzwingen (neu bauen bzw. dist neu einspielen)
#   --run-migrations     alembic upgrade head erzwingen (inkl. Prüfung)
#   --backup-only        nur Snapshot anlegen, sonst nichts ändern
#   --rollback[=DIR]     auf den neuesten (oder angegebenen) Snapshot zurückrollen
#   --list-backups       vorhandene Snapshots anzeigen
#   --force              alle Komponenten neu einspielen (Update)
#   --update             Update erzwingen (Fehler, wenn nichts installiert ist)
#   --install            vollständige Neueinrichtung über eine bestehende Installation
#                        (Daten + Konfiguration bleiben erhalten)
#   --interactive        Admin-Passwort, Firewall und Netzwerk interaktiv abfragen
#   -h, --help           diese Hilfe
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
# Per "curl | bash" gibt es keine Skriptdatei (BASH_SOURCE leer) -> SRC_DIR bleibt leer
# und bootstrap_source() lädt die Quelle herunter.
SRC_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
# Zustandsdateien des Installers (Version, Hash der Python-Abhängigkeiten,
# Hash der Frontend-Quellen, Hashes der zuletzt ausgelieferten Konfigurationsdateien).
STATE_DIR="${APP_DIR}/.install-state"
# Update-Snapshots (Code + Konfiguration + DB), je Lauf ein Unterverzeichnis.
SNAPSHOT_ROOT="${DATA_DIR}/backups"
KEEP_SNAPSHOTS="${NETPROBE_KEEP_SNAPSHOTS:-5}"

NETPROBE_REPO="${NETPROBE_REPO:-mwieschen/netprobe}"
NETPROBE_REF="${NETPROBE_REF:-}"
NETPROBE_SOURCE_URL="${NETPROBE_SOURCE_URL:-}"

# Wird in choose_mode() gesetzt: "install" oder "update".
MODE=""
# 1 = vorgebautes Frontend aus dem Release-Paket, 0 = Frontend wird gebaut.
FRONTEND_PREBUILT=0
NEW_VERSION=""
BOOTSTRAP_DIR=""

# Optionen
INTERACTIVE=0
OPT_REBUILD_FRONTEND=0
OPT_RUN_MIGRATIONS=0
OPT_BACKUP_ONLY=0
OPT_FORCE=0
OPT_ROLLBACK=""        # "" = nein, "latest" oder Snapshot-Verzeichnis
OPT_LIST_BACKUPS=0

# Ergebnis der Änderungserkennung (plan_update)
CHANGED_BACKEND=0
CHANGED_DEPS=0
CHANGED_FRONTEND=0
CHANGED_UNIT=0
MIGRATIONS_PENDING=0
MIGRATION_FROM=""
MIGRATION_TO=""
VENV_BROKEN=0
SNAPSHOT_DIR=""
# Wird von setup_admin() gesetzt, wenn ein Zufallspasswort erzeugt wurde (nur Ausgabe
# in der Zusammenfassung, niemals in eine Datei oder ein Log).
GENERATED_ADMIN_USER=""
GENERATED_ADMIN_PASS=""

C_OK="\033[0;32m"; C_WARN="\033[0;33m"; C_ERR="\033[0;31m"; C_INFO="\033[0;36m"; C_RST="\033[0m"
C_BOLD="\033[1m"
log()  { echo -e "${C_INFO}[netprobe]${C_RST} $*"; }
ok()   { echo -e "${C_OK}[  ok  ]${C_RST} $*"; }
warn() { echo -e "${C_WARN}[ warn ]${C_RST} $*"; }
die()  { echo -e "${C_ERR}[ fehler ]${C_RST} $*" >&2; exit 1; }

STEP=0
STEPS_TOTAL=0
step() {
    STEP=$((STEP + 1))
    echo
    echo -e "${C_BOLD}${C_INFO}==> [${STEP}/${STEPS_TOTAL}] $*${C_RST}"
}

# Prüft freien Speicher auf dem Dateisystem von <pfad>; bricht mit Meldung ab.
require_space() {
    local path="$1" need_mb="$2" what="$3" free_mb
    while [ ! -e "$path" ]; do path="$(dirname "$path")"; done
    free_mb="$(df -Pm "$path" | awk 'NR==2 {print $4}')"
    if [ "${free_mb:-0}" -lt "$need_mb" ]; then
        die "Zu wenig freier Speicher für ${what} auf $(df -P "$path" | awk 'NR==2 {print $6}') (${free_mb} MB frei, benötigt: ${need_mb} MB)."
    fi
}

export DEBIAN_FRONTEND=noninteractive

# Wird in detect_os() gesetzt: Codename für Drittanbieter-Repos (Ookla, NodeSource).
# Ookla führt unter packagecloud.io/ookla/speedtest-cli/ubuntu derzeit WEDER 'noble'
# (24.04) NOCH 'resolute' (26.04) - beide Codenamen liefern dort keine Release-Datei,
# wodurch "apt-get update" mit "does not have a Release file" hart fehlschlägt (und mit
# set -e die gesamte Installation abbricht - auch bei jedem künftigen Aufruf, bis die
# fehlerhafte Paketquelle wieder entfernt wird). Für 24.04/26.04 wird deshalb auf die
# letzte von Ookla sicher geführte LTS (jammy/22.04) zurückgefallen; NodeSource erkennt
# den Codename selbst und braucht diesen Fallback nicht.
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
            # Ookla führt 'noble' nicht -> jammy-Fallback (siehe Kommentar oben).
            REPO_CODENAME="jammy"
            ok "Ubuntu ${UBUNTU_VERSION_ID} (${UBUNTU_CODENAME:-noble}) erkannt."
            warn "Für Ookla wird der 22.04-Fallback ('${REPO_CODENAME}') verwendet, da deren Repo '${UBUNTU_CODENAME:-noble}' nicht führt." ;;
        26.04)
            # Ookla führt 'resolute' nicht -> jammy-Fallback (siehe Kommentar oben).
            REPO_CODENAME="jammy"
            ok "Ubuntu ${UBUNTU_VERSION_ID} (${UBUNTU_CODENAME:-unbekannt}) erkannt."
            warn "Für Ookla wird der 22.04-Fallback ('${REPO_CODENAME}') verwendet, da deren Repo 26.04 noch nicht führt." ;;
        *)
            REPO_CODENAME="jammy"
            warn "Ubuntu ${UBUNTU_VERSION_ID:-?} ist ungetestet (unterstützt: 22.04, 24.04, 26.04)."
            warn "Nutze Repo-Fallback '${REPO_CODENAME}'; die Installation wird dennoch versucht." ;;
    esac
}

# ---------------------------------------------------------------------------
# 1. Vorbedingungen prüfen
# ---------------------------------------------------------------------------
check_prereqs() {
    log "Prüfe Vorbedingungen…"
    detect_os

    log "Prüfe Internetverbindung…"
    if ! curl -fsS --max-time 10 https://pypi.org >/dev/null 2>&1 && \
       ! curl -fsS --max-time 10 https://archive.ubuntu.com >/dev/null 2>&1; then
        if [ "$MODE" = "install" ]; then
            die "Keine Internetverbindung. Für die Installation werden Pakete benötigt."
        fi
        warn "Keine Internetverbindung – Update läuft weiter, solange keine neuen Pakete nötig sind."
    else
        ok "Internetverbindung vorhanden."
    fi

    # Freier Speicher (Neuinstallation mind. 2 GB, Update mind. 500 MB auf / )
    local free_mb need_mb=2048
    [ "$MODE" = "update" ] && need_mb=500
    free_mb=$(df -Pm / | awk 'NR==2 {print $4}')
    if [ "${free_mb:-0}" -lt "$need_mb" ]; then
        die "Zu wenig freier Speicher auf / (${free_mb} MB, benötigt: ${need_mb} MB)."
    fi
    ok "Freier Speicher: ${free_mb} MB."

    # Port-80-Konflikt (außer durch nginx selbst)
    if [ "$MODE" = "install" ] && ss -ltnp 2>/dev/null | grep -q ':80 '; then
        if ! ss -ltnp 2>/dev/null | grep ':80 ' | grep -q 'nginx'; then
            warn "Port 80 ist bereits durch einen anderen Dienst belegt. nginx kann nicht starten."
            warn "Bitte den Konflikt auflösen oder den anderen Dienst stoppen."
        fi
    fi
}

# Entfernt eine von einem früheren Lauf hinterlassene, ungültige Ookla-Paketquelle
# (falscher, von Ookla nicht geführter Codename wie 'noble'/'resolute'), BEVOR
# apt-get update aufgerufen wird. Eine solche Quelle lässt "apt-get update" dauerhaft
# mit "does not have a Release file" fehlschlagen - bei JEDEM künftigen Aufruf, nicht
# nur hier, UNABHÄNGIG davon ob speedtest zwischenzeitlich über das Tarball-Fallback
# installiert wurde. Erkennung daher über den Dateiinhalt (enthält er NICHT den
# aktuell korrekten Codename 'jammy', ist er veraltet/falsch) statt über den
# Installationsstatus von speedtest - so bleibt eine bereits funktionierende Quelle
# (z. B. auf 22.04, wo Ookla den nativen Codename 'jammy' ohnehin führt) unberührt,
# während eine kaputte Quelle auch dann entfernt wird, wenn speedtest längst per
# Tarball läuft.
cleanup_stale_ookla_repo() {
    shopt -s nullglob
    local f list_files=(/etc/apt/sources.list.d/*ookla*.list)
    for f in "${list_files[@]}"; do
        grep -q "jammy" "$f" 2>/dev/null && continue
        rm -f "$f"
        rm -f /etc/apt/keyrings/*ookla*.gpg /usr/share/keyrings/*ookla*.gpg \
            /etc/apt/trusted.gpg.d/*ookla*.gpg
    done
    shopt -u nullglob
}

# ---------------------------------------------------------------------------
# 2. Pakete installieren
# ---------------------------------------------------------------------------
APT_PACKAGES=(
    python3 python3-venv python3-dev
    build-essential
    nginx
    sqlite3
    tshark wireshark-common
    fping iputils-ping mtr-tiny traceroute iperf3
    curl jq tar ca-certificates gnupg lsb-release ssl-cert openssl
    libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
)

# Gibt die noch nicht installierten Pakete aus APT_PACKAGES aus.
_missing_packages() {
    local p
    for p in "${APT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || echo "$p"
    done
}

install_packages() {
    local missing
    missing="$(_missing_packages | tr '\n' ' ')"
    if [ -z "${missing// /}" ]; then
        # Alles vorhanden -> kein "apt-get update", kein Download (schnelles Update).
        ok "Alle Systempakete bereits installiert."
    else
        cleanup_stale_ookla_repo
        log "Aktualisiere Paketquellen…"
        apt-get update -qq

        log "Installiere fehlende Systempakete: ${missing}"
        # wireshark-common vorab so konfigurieren, dass dumpcap durch Nicht-root
        # nutzbar ist (setuid für Nutzer der Gruppe 'wireshark').
        echo "wireshark-common wireshark-common/install-setuid boolean true" | debconf-set-selections
        # iperf3 NICHT automatisch als Daemon starten (die netprobe-UI steuert den Server).
        echo "iperf3 iperf3/start_daemon boolean false" | debconf-set-selections
        # shellcheck disable=SC2086
        apt-get install -y -qq $missing
        ok "Systempakete installiert."
    fi

    ensure_python
    # Node.js wird nur gebraucht, wenn das Frontend auf dem Server gebaut werden muss.
    if [ "$FRONTEND_PREBUILT" -eq 0 ] && [ "$CHANGED_FRONTEND" -eq 1 ]; then
        install_nodejs
    fi
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

    # 1) APT-Repository (packagecloud). Der Codename wird auf REPO_CODENAME gesetzt
    #    (siehe detect_os(): jammy-Fallback auf 24.04/26.04, da Ookla dort aktuell
    #    keine Release-Datei führt). Das Skript honoriert os/dist.
    if curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh \
        | os=ubuntu dist="${REPO_CODENAME}" bash - >/dev/null 2>&1; then
        apt-get install -y -qq speedtest >/dev/null 2>&1 || true
    fi

    # 2) Fallback: offizielles Tarball direkt von Ookla installieren.
    if ! command -v speedtest >/dev/null 2>&1; then
        warn "APT-Installation der Speedtest CLI fehlgeschlagen; versuche offizielles Tarball…"
        # Angelegte, aber nicht funktionsfähige Paketquelle wieder entfernen, damit sie
        # nicht jeden künftigen "apt-get update" bricht (siehe cleanup_stale_ookla_repo()).
        cleanup_stale_ookla_repo
        install_speedtest_tarball || true
    fi

    if command -v speedtest >/dev/null 2>&1; then
        ok "Speedtest CLI installiert ($(speedtest --version 2>/dev/null | head -n1 || echo ok)) -> $(command -v speedtest)."
    else
        warn "Speedtest CLI konnte NICHT installiert werden. Speedtests bleiben deaktiviert,"
        warn "bis die CLI vorhanden ist (siehe README, Abschnitt 'Speedtest CLI manuell')."
    fi
}

# Akzeptiert Lizenz/GDPR der Ookla-CLI EINMALIG im Kontext des netprobe-Users und
# legt die Konfiguration unter dessen HOME (/var/lib/netprobe/.config/ookla) an.
# Ohne das schlägt der erste Speedtest aus dem Dienst fehl, weil ProtectSystem=strict
# das Schreiben der Zustimmung verhindert. Idempotent (mehrfach ausführbar).
seed_speedtest_license() {
    command -v speedtest >/dev/null 2>&1 || return 0
    id "$APP_USER" >/dev/null 2>&1 || return 0
    log "Akzeptiere Ookla-Lizenz für Benutzer '${APP_USER}'…"
    install -d -o "$APP_USER" -g "$APP_GROUP" -m 700 "$DATA_DIR/.config" "$DATA_DIR/.config/ookla"
    sudo -u "$APP_USER" env \
        HOME="$DATA_DIR" \
        XDG_CONFIG_HOME="$DATA_DIR/.config" \
        XDG_CACHE_HOME="$DATA_DIR/.cache" \
        speedtest --accept-license --accept-gdpr --progress=no --version >/dev/null 2>&1 \
        && ok "Ookla-Lizenz akzeptiert." \
        || warn "Lizenz-Vorabbestätigung fehlgeschlagen; Speedtest versucht es beim ersten Lauf erneut."
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
# /opt/netprobe gehört root und ist für den Dienst nur lesbar: root führt daraus
# install.sh sowie venv-Binaries (pip/alembic) aus – wäre der Baum dem Dienstkonto
# beschreibbar, könnte ein kompromittierter Dienst sich beim nächsten Update root-
# Rechte verschaffen. Zur Laufzeit ist /opt/netprobe ohnehin read-only
# (ProtectSystem=strict); beschreibbar sind nur DATA_DIR/LOG_DIR.
secure_app_dir() {
    [ -d "$APP_DIR" ] || return 0
    chown -R root:root "$APP_DIR"
    chmod -R u+rwX,go+rX,go-w "$APP_DIR"
}

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

    # Basisverzeichnisse. HOME des Dienstes ist /var/lib/netprobe (siehe systemd-Unit);
    # .config/.cache werden von der Ookla Speedtest CLI benötigt, reports/ von der
    # Report-Funktion.
    mkdir -p "$APP_DIR" "$DATA_DIR" "$CAPTURE_DIR" "$LOG_DIR" "$ETC_DIR" \
             "$DATA_DIR/.config/ookla" "$DATA_DIR/.cache" "$DATA_DIR/reports" \
             "$DATA_DIR/geoip"
    chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR" "$LOG_DIR"
    secure_app_dir
    chmod 750 "$DATA_DIR" "$LOG_DIR" "$DATA_DIR/reports" "$DATA_DIR/geoip"
    chmod 700 "$DATA_DIR/.config" "$DATA_DIR/.cache"

    # Capture-Verzeichnis: Eigentümer netprobe, Gruppe 'wireshark' (falls vorhanden),
    # Modus 2770 (setgid) -> von dumpcap erzeugte PCAP-Dateien erben die Gruppe
    # 'wireshark', sodass sowohl der Dienst als auch Administratoren in dieser Gruppe
    # (z. B. für manuelle 'tshark -w …'-Tests) schreiben/lesen können. Idempotent.
    if getent group wireshark >/dev/null 2>&1; then
        chown "$APP_USER:wireshark" "$CAPTURE_DIR"
        chmod 2770 "$CAPTURE_DIR"
    else
        chown "$APP_USER:$APP_GROUP" "$CAPTURE_DIR"
        chmod 2750 "$CAPTURE_DIR"
    fi
    ok "Verzeichnisse angelegt (Captures: $(stat -c '%U:%G %a' "$CAPTURE_DIR"))."

    # Capabilities für Nicht-root-Messungen:
    # - Der systemd-Dienst erhält CAP_NET_RAW/CAP_NET_ADMIN über AmbientCapabilities
    #   (siehe deploy/systemd/netprobe.service); das ist der maßgebliche Pfad, weil
    #   NoNewPrivileges=true File-Capabilities beim exec unwirksam macht.
    # - Zusätzlich werden die File-Capabilities auf den Binaries gesetzt, damit auch
    #   manuelle Aufrufe (Shell, Cronjobs) ohne sudo funktionieren. Fehlschlag ist
    #   nicht kritisch. Idempotent.
    setcap 'cap_net_raw,cap_net_admin=eip' "$(command -v dumpcap || echo /usr/bin/dumpcap)" 2>/dev/null \
        || warn "setcap auf dumpcap fehlgeschlagen; manuelle Captures ggf. eingeschränkt (Dienst nutzt AmbientCapabilities)."
    setcap 'cap_net_raw+ep' "$(command -v fping || echo /usr/bin/fping)" 2>/dev/null || true
    setcap 'cap_net_raw+ep' "$(command -v ping  || echo /usr/bin/ping)"  2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 4. Anwendung ausrollen
# ---------------------------------------------------------------------------
# Pfade (relativ zum Quell- bzw. Installationsverzeichnis), die zum "Backend" gehören.
# Gleiche relative Struktur unter SRC_DIR und APP_DIR -> direkt per md5 vergleichbar.
BACKEND_PATHS=(backend/app backend/alembic backend/alembic.ini backend/pyproject.toml
               scripts/backup.sh scripts/restore.sh scripts/fix-permissions.sh)

# md5 über einen Dateibaum: relative Pfade + Inhalte, ohne Bytecode-Caches. Fehlende
# Pfade werden ignoriert (fehlt die Installation, unterscheidet sich der Hash).
_tree_md5() {
    local base="$1"; shift
    (
        cd "$base" 2>/dev/null || exit 0
        find "$@" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' \
            ! -path '*/node_modules/*' ! -path './dist/*' 2>/dev/null \
            | LC_ALL=C sort | xargs -r -d '\n' md5sum
    ) | md5sum | cut -d' ' -f1
}

# Hash der Frontend-Quellen eines Repository-Checkouts (ohne node_modules/dist).
_frontend_src_md5() { _tree_md5 "$SRC_DIR/frontend" .; }

backend_code_changed() {
    [ "$(_tree_md5 "$SRC_DIR" "${BACKEND_PATHS[@]}")" != "$(_tree_md5 "$APP_DIR" "${BACKEND_PATHS[@]}")" ]
}

frontend_code_changed() {
    [ -f "$APP_DIR/frontend/dist/index.html" ] || return 0
    if [ "$FRONTEND_PREBUILT" -eq 1 ]; then
        # Release-Paket: vorgebautes dist direkt mit dem installierten vergleichen.
        [ "$(_tree_md5 "$SRC_DIR/frontend" dist)" != "$(_tree_md5 "$APP_DIR/frontend" dist)" ]
    else
        # Repository-Checkout: Quell-Hash mit dem Hash des zuletzt gebauten Stands vergleichen.
        [ "$(_frontend_src_md5)" != "$(cat "$STATE_DIR/frontend-src.md5" 2>/dev/null)" ]
    fi
}

python_deps_changed() {
    [ "$VENV_BROKEN" -eq 1 ] && return 0
    [ "$(sha256sum "$SRC_DIR/backend/pyproject.toml" | cut -d' ' -f1)" \
        != "$(cat "$STATE_DIR/deps.sha256" 2>/dev/null)" ]
}

systemd_unit_changed() {
    [ "$(sha256sum < "$SRC_DIR/deploy/systemd/netprobe.service")" \
        != "$(sha256sum < /etc/systemd/system/netprobe.service 2>/dev/null)" ]
}

venv_healthy() {
    [ -x "$VENV_DIR/bin/python" ] && \
        "$VENV_DIR/bin/python" -c 'import fastapi, sqlalchemy, alembic, gunicorn' >/dev/null 2>&1
}

# --- Alembic-Migrationsstand ------------------------------------------------
# Revisionen aus der alembic-Ausgabe ("0013_xyz (head)") -> sortierte Liste.
_alembic_revs() { awk '{print $1}' | grep -E '^[A-Za-z0-9_]+$' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//'; }

# Aktueller DB-Stand (als Dienstbenutzer, damit keine root-eigenen WAL-Dateien entstehen).
alembic_current() {
    ( cd "$APP_DIR/backend" && sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" \
        PYTHONDONTWRITEBYTECODE=1 "$VENV_DIR/bin/alembic" current 2>/dev/null ) | _alembic_revs
}

# Ziel-Revisionen eines Backend-Verzeichnisses ("alembic heads" liest nur die Skripte,
# verbindet sich nicht mit der DB).
alembic_heads() {
    ( cd "$1" && PYTHONDONTWRITEBYTECODE=1 "$VENV_DIR/bin/alembic" heads 2>/dev/null ) | _alembic_revs
}

# 0 = Migrationen ausstehend (DB-Stand != heads des angegebenen Backends).
migrations_pending() {
    local backend_dir="${1:-$APP_DIR/backend}"
    MIGRATION_FROM="$(alembic_current || true)"
    MIGRATION_TO="$(alembic_heads "$backend_dir" || true)"
    [ -z "$MIGRATION_TO" ] && return 0        # unbekannt -> sicherheitshalber migrieren
    [ "$MIGRATION_FROM" != "$MIGRATION_TO" ]
}

deploy_backend() {
    log "Spiele Backend-Code nach ${APP_DIR} ein…"
    mkdir -p "$APP_DIR/backend" "$APP_DIR/frontend" "$STATE_DIR"
    # Verzeichnisse komplett ersetzen (nicht drüberkopieren), damit gelöschte Module
    # oder Migrationen aus Vorversionen nicht als Altlast liegen bleiben.
    rm -rf "$APP_DIR/backend/app" "$APP_DIR/backend/alembic"
    cp -r "$SRC_DIR/backend/app" "$SRC_DIR/backend/alembic" "$APP_DIR/backend/"
    cp "$SRC_DIR/backend/alembic.ini" "$SRC_DIR/backend/pyproject.toml" "$APP_DIR/backend/"
    find "$APP_DIR/backend" -type d -name __pycache__ -prune -exec rm -rf {} +
    # Betriebsskripte (backup/restore/fix-permissions) mit ausliefern.
    mkdir -p "$APP_DIR/scripts"
    cp "$SRC_DIR"/scripts/{backup,restore,fix-permissions}.sh "$APP_DIR/scripts/" 2>/dev/null || true
    chmod 755 "$APP_DIR"/scripts/*.sh 2>/dev/null || true
    secure_app_dir
    ok "Backend-Code eingespielt."
}

# Hält die installierte Version fest (nach erfolgreichem Lauf).
record_version() {
    mkdir -p "$STATE_DIR"
    echo "$NEW_VERSION" > "$STATE_DIR/version"
    secure_app_dir
}

# Python-Abhängigkeiten: vollständige Installation nur bei neuem venv oder
# geändertem pyproject.toml. Sonst wird nur das netprobe-Paket selbst offline
# (--no-deps, --no-build-isolation) neu eingespielt – kein Download nötig.
install_python_deps() {
    local new_hash old_hash=""
    new_hash="$(sha256sum "$APP_DIR/backend/pyproject.toml" | cut -d' ' -f1)"
    [ -f "$STATE_DIR/deps.sha256" ] && old_hash="$(cat "$STATE_DIR/deps.sha256")"

    # Z. B. nach einem Ubuntu-Release-Upgrade (neue Python-Version) zeigt das venv ins Leere.
    if [ -d "$VENV_DIR" ] && ! venv_healthy; then
        warn "Virtuelle Python-Umgebung ist beschädigt – wird neu erstellt."
        rm -rf "$VENV_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        log "Erstelle virtuelle Python-Umgebung…"
        python3 -m venv "$VENV_DIR"
        old_hash=""
    fi

    if [ "$new_hash" = "$old_hash" ] && "$VENV_DIR/bin/python" -c 'import setuptools' 2>/dev/null; then
        "$VENV_DIR/bin/pip" install --quiet --no-deps --no-build-isolation --force-reinstall \
            "$APP_DIR/backend"
        ok "Python-Abhängigkeiten unverändert – nur netprobe-Paket aktualisiert."
        return
    fi

    log "Installiere Python-Abhängigkeiten…"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
    # WeasyPrint (Extra "pdf") für Report-PDFs ist optional; schlägt es fehl,
    # wird ohne Extra installiert (Reports dann nur als HTML).
    if "$VENV_DIR/bin/pip" install --quiet "$APP_DIR/backend[pdf]" 2>/dev/null; then
        ok "Python-Abhängigkeiten installiert (inkl. WeasyPrint für Report-PDF)."
    else
        "$VENV_DIR/bin/pip" install --quiet "$APP_DIR/backend"
        warn "WeasyPrint nicht installiert; Reports werden nur als HTML erzeugt."
    fi
    echo "$new_hash" > "$STATE_DIR/deps.sha256"
}

# Frontend: aus dem Release-Paket vorgebaut übernehmen, sonst im Temp-Verzeichnis
# bauen (ohne lokales node_modules/dist mitzukopieren). Der Austausch erfolgt über
# ein Nachbarverzeichnis, damit nie ein halb kopiertes dist ausgeliefert wird.
deploy_frontend() {
    local src_dist build_dir="" src_hash=""
    mkdir -p "$APP_DIR/frontend" "$STATE_DIR"
    if [ "$FRONTEND_PREBUILT" -eq 1 ]; then
        src_dist="$SRC_DIR/frontend/dist"
        ok "Verwende vorgebautes Frontend aus dem Release-Paket."
    else
        # npm ci + node_modules + Build brauchen grob 1 GB im Temp-Verzeichnis.
        require_space "${TMPDIR:-/tmp}" 1024 "den Frontend-Build"
        require_space "$APP_DIR" 100 "das Frontend"
        src_hash="$(_frontend_src_md5)"
        log "Baue Frontend (Vite)…"
        build_dir="$(mktemp -d)"
        ( cd "$SRC_DIR/frontend" && tar --exclude=./node_modules --exclude=./dist -cf - . ) \
            | tar -xf - -C "$build_dir"
        ( cd "$build_dir" && { npm ci --silent 2>/dev/null || npm install --silent; } )
        ( cd "$build_dir" && npm run build --silent )
        src_dist="$build_dir/dist"
        ok "Frontend gebaut."
    fi
    rm -rf "$APP_DIR/frontend/dist.new"
    cp -r "$src_dist" "$APP_DIR/frontend/dist.new"
    rm -rf "$APP_DIR/frontend/dist"
    mv "$APP_DIR/frontend/dist.new" "$APP_DIR/frontend/dist"
    [ -n "$build_dir" ] && rm -rf "$build_dir"
    if [ -n "$src_hash" ]; then
        echo "$src_hash" > "$STATE_DIR/frontend-src.md5"
    fi
    secure_app_dir
    return 0
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
    # Explizites "|| return 1": Im ||-Kontext des Aufrufers greift set -e nicht.
    ( cd "$APP_DIR/backend" && \
      sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" "$VENV_DIR/bin/alembic" upgrade head ) \
      || return 1
    # Erfolg erst bestätigen, wenn der DB-Stand tatsächlich den heads entspricht.
    if migrations_pending "$APP_DIR/backend"; then
        warn "Nach der Migration: DB-Stand '${MIGRATION_FROM:-leer}' != Ziel '${MIGRATION_TO:-?}'."
        return 1
    fi
    ok "Datenbank ist auf dem aktuellen Stand (${MIGRATION_TO})."
}

setup_admin() {
    # Existiert bereits ein Benutzer? Dann nichts tun (idempotent).
    local count count_script
    count_script="$(mktemp)"
    cat > "$count_script" <<'PY'
from app.db.session import SessionLocal
from app.models.user import User
db = SessionLocal()
print(db.query(User).count())
db.close()
PY
    chmod 0644 "$count_script"
    count="$( ( cd "$APP_DIR/backend" && sudo -u "$APP_USER" env NETPROBE_ENV_FILE="$ENV_FILE" \
        "$VENV_DIR/bin/python" "$count_script" ) 2>/dev/null || echo 0 )"
    rm -f "$count_script"
    if [ "${count:-0}" -gt 0 ]; then
        ok "Es existiert bereits mindestens ein Benutzer – Admin-Anlage übersprungen."
        return
    fi

    local admin_user admin_pass="" admin_pass2
    if [ "$INTERACTIVE" -eq 1 ]; then
        echo
        log "Initialen Admin-Benutzer anlegen."
        read -r -p "Admin-Benutzername [admin]: " admin_user
        admin_user="${admin_user:-admin}"
        while true; do
            read -r -s -p "Admin-Passwort (min. 8 Zeichen): " admin_pass; echo
            read -r -s -p "Passwort bestätigen: " admin_pass2; echo
            if [ "$admin_pass" != "$admin_pass2" ]; then warn "Passwörter stimmen nicht überein."; continue; fi
            if [ "${#admin_pass}" -lt 8 ]; then warn "Passwort zu kurz."; continue; fi
            break
        done
    else
        admin_user="${NETPROBE_ADMIN_USER:-admin}"
        admin_pass="${NETPROBE_ADMIN_PASSWORD:-}"
        if [ -n "$admin_pass" ] && [ "${#admin_pass}" -lt 8 ]; then
            die "NETPROBE_ADMIN_PASSWORD ist zu kurz (min. 8 Zeichen)."
        fi
        if [ -z "$admin_pass" ]; then
            # Zufallspasswort; wird nur einmal in der Zusammenfassung angezeigt.
            admin_pass="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
            GENERATED_ADMIN_USER="$admin_user"
            GENERATED_ADMIN_PASS="$admin_pass"
        fi
    fi

    # Passwort nur über die Umgebung weiterreichen (sudo --preserve-env), nie als
    # Argument -> nicht in ps//proc/<pid>/cmdline sichtbar; wird nirgends geloggt.
    local out
    out="$( cd "$APP_DIR/backend" && \
        NETPROBE_ADMIN_USER="$admin_user" NETPROBE_ADMIN_PASSWORD="$admin_pass" \
        sudo -u "$APP_USER" --preserve-env=NETPROBE_ADMIN_USER,NETPROBE_ADMIN_PASSWORD \
            env NETPROBE_ENV_FILE="$ENV_FILE" "$VENV_DIR/bin/python" -m app.cli ensure-admin )" || true
    unset admin_pass admin_pass2
    if echo "$out" | grep -q "angelegt"; then
        ok "Admin-Benutzer '${admin_user}' angelegt."
        # Sofort zeigen (nicht nur in der Zusammenfassung), falls ein späterer Schritt abbricht.
        if [ -n "$GENERATED_ADMIN_PASS" ]; then
            echo -e "         ${C_BOLD}Initiales Passwort: ${GENERATED_ADMIN_PASS}${C_RST}  (nur jetzt sichtbar – bitte notieren)"
        fi
    else
        # Nicht angelegt (z. B. existiert doch schon ein Benutzer) -> Zufallspasswort verwerfen.
        GENERATED_ADMIN_USER=""; GENERATED_ADMIN_PASS=""
        warn "Admin-Benutzer nicht angelegt: ${out:-keine Ausgabe}"
        warn "Manuell: cd ${APP_DIR}/backend && sudo -u ${APP_USER} env NETPROBE_ENV_FILE=${ENV_FILE} ${VENV_DIR}/bin/python -m app.cli create-admin --username admin"
    fi
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

setup_iperf3_service() {
    # Optionaler Dauerbetrieb-Server. Wird installiert, aber NICHT aktiviert –
    # standardmäßig steuert die netprobe-UI einen eigenen iperf3-Subprozess.
    # Aktivieren mit:  sudo systemctl enable --now netprobe-iperf3
    cp "$SRC_DIR/deploy/systemd/netprobe-iperf3.service" /etc/systemd/system/netprobe-iperf3.service
    systemctl daemon-reload
    if systemctl is-enabled --quiet netprobe-iperf3 2>/dev/null; then
        systemctl restart netprobe-iperf3 || true
        ok "iperf3-Server-Dienst (netprobe-iperf3) aktualisiert und aktiv."
    else
        ok "iperf3-Server-Dienst installiert (inaktiv; 'systemctl enable --now netprobe-iperf3')."
    fi
    # Den vom iperf3-Paket mitgelieferten Dienst NICHT automatisch starten.
    systemctl disable --now iperf3 >/dev/null 2>&1 || true
}

setup_geoip() {
    # Traceroute-Hop-Geolokalisierung ist hybrid: zuerst lokale MaxMind-DB, sonst
    # ip-api.com (HTTP). Ohne lokale DB funktioniert alles über die Online-Abfrage.
    # Wenn NETPROBE_MAXMIND_LICENSE_KEY gesetzt ist, werden die GeoLite2-DBs geladen.
    local geo_dir="$DATA_DIR/geoip"
    if [ -n "${NETPROBE_MAXMIND_LICENSE_KEY:-}" ]; then
        local base="https://download.maxmind.com/app/geoip_download"
        local edition
        for edition in GeoLite2-City GeoLite2-ASN; do
            log "Lade ${edition}.mmdb von MaxMind…"
            if curl -fsSL "${base}?edition_id=${edition}&license_key=${NETPROBE_MAXMIND_LICENSE_KEY}&suffix=tar.gz" \
                    -o "/tmp/${edition}.tar.gz" 2>/dev/null; then
                tar -xzf "/tmp/${edition}.tar.gz" -C /tmp
                find /tmp -name "${edition}.mmdb" -exec cp {} "${geo_dir}/${edition}.mmdb" \; 2>/dev/null || true
                rm -rf "/tmp/${edition}.tar.gz" "/tmp/${edition}_"* 2>/dev/null || true
            else
                warn "${edition} konnte nicht geladen werden (Lizenzschlüssel prüfen)."
            fi
        done
        chown -R "$APP_USER:$APP_GROUP" "$geo_dir"
        ok "GeoLite2-Datenbanken in ${geo_dir} abgelegt."
    else
        ok "Keine lokale GeoIP-DB (NETPROBE_MAXMIND_LICENSE_KEY nicht gesetzt) – Traceroute nutzt ip-api.com."
    fi
}

# Installiert eine Konfigurationsdatei wie dpkg ein "conffile": Hat der Admin die
# zuletzt ausgelieferte Version lokal angepasst (z. B. TLS in der nginx-Site),
# wird sie NICHT überschrieben – die neue Version landet daneben als <datei>.netprobe-new.
install_conffile() {
    local src="$1" dst="$2" key cur new last=""
    key="$(basename "$dst")"
    new="$(sha256sum "$src" | cut -d' ' -f1)"
    mkdir -p "$STATE_DIR/conffiles"
    [ -f "$STATE_DIR/conffiles/$key" ] && last="$(cat "$STATE_DIR/conffiles/$key")"
    if [ -f "$dst" ]; then
        cur="$(sha256sum "$dst" | cut -d' ' -f1)"
        if [ "$cur" != "$new" ] && [ "$cur" != "$last" ] && [ "$MODE" = "update" ]; then
            cp "$src" "${dst}.netprobe-new"
            warn "${dst} wurde lokal angepasst und bleibt unverändert."
            warn "Neue Vorlage: ${dst}.netprobe-new (bitte Unterschiede manuell übernehmen)."
            return 0
        fi
    fi
    cp "$src" "$dst"
    rm -f "${dst}.netprobe-new"
    echo "$new" > "$STATE_DIR/conffiles/$key"
}

# HTTPS für nginx (Port 443). Wachtl-Probes sprechen nur HTTPS und prüfen statt der
# Zertifikatskette den SPKI-Pin, also den öffentlichen Schlüssel. Der Schlüssel wird daher
# nur EINMAL erzeugt und nie ersetzt; das Zertifikat wird mit demselben Schlüssel erneuert,
# wenn es fehlt oder in weniger als 30 Tagen abläuft. So bleibt der Pin dauerhaft gleich.
setup_tls() {
    local dir="$ETC_DIR/tls" key cert san host ip
    key="$dir/privkey.pem"; cert="$dir/fullchain.pem"
    install -d -m 0755 -o root -g root "$dir"
    if [ ! -s "$key" ]; then
        log "Erzeuge TLS-Schlüssel (ECDSA P-256)…"
        (umask 077; openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$key")             || { warn "TLS-Schlüssel konnte nicht erzeugt werden."; return 0; }
    fi
    chown root:root "$key"; chmod 0600 "$key"
    if [ -s "$cert" ] && openssl x509 -in "$cert" -noout -checkend 2592000 >/dev/null 2>&1; then
        ok "TLS-Zertifikat vorhanden (${cert})."
    else
        host="$(hostname -s 2>/dev/null || echo netprobe)"
        san="DNS:${host},DNS:$(hostname -f 2>/dev/null || echo "$host"),DNS:localhost,IP:127.0.0.1"
        for ip in $(hostname -I 2>/dev/null); do
            case "$ip" in *:*) ;; *) san="${san},IP:${ip}" ;; esac
        done
        log "Erzeuge selbstsigniertes TLS-Zertifikat (10 Jahre)…"
        openssl req -x509 -new -key "$key" -out "$cert" -days 3650 -subj "/CN=${host}"             -addext "subjectAltName=${san}" >/dev/null 2>&1             || { warn "TLS-Zertifikat konnte nicht erzeugt werden."; return 0; }
        ok "TLS-Zertifikat erzeugt (${cert})."
    fi
    # Das Zertifikat ist öffentlich: netprobe liest es, um den Pin für das Setup anzuzeigen.
    chown root:root "$cert"; chmod 0644 "$cert"
}

setup_nginx() {
    log "Konfiguriere nginx…"
    install_conffile "$SRC_DIR/deploy/nginx/netprobe.conf" /etc/nginx/sites-available/netprobe.conf
    ln -sf /etc/nginx/sites-available/netprobe.conf /etc/nginx/sites-enabled/netprobe.conf
    rm -f /etc/nginx/sites-enabled/default
    if nginx -t >/dev/null 2>&1; then
        # reload-or-restart startet ein gestopptes nginx auch (Selbstheilung).
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl reload-or-restart nginx
        ok "nginx konfiguriert."
    else
        warn "nginx-Konfigurationstest fehlgeschlagen; bitte 'nginx -t' prüfen."
    fi
}

setup_logrotate() {
    install_conffile "$SRC_DIR/deploy/logrotate/netprobe" /etc/logrotate.d/netprobe
    ok "logrotate konfiguriert."
}

setup_ufw() {
    command -v ufw >/dev/null 2>&1 || return 0
    if [ "$INTERACTIVE" -eq 0 ]; then
        # Ohne Rückfrage nur handeln, wenn UFW aktiv ist – sonst wäre die UI unerreichbar.
        if ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw allow 80/tcp >/dev/null 2>&1 || true
            ufw allow 443/tcp >/dev/null 2>&1 || true
            ok "UFW ist aktiv – Regeln für Port 80/443 hinzugefügt."
        else
            ok "UFW ist nicht aktiv – Firewall bleibt unverändert."
        fi
        return 0
    fi
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
# Wartet bis zu 30 s auf den Backend-Healthcheck. Rückgabe 0 nur, wenn das Backend
# antwortet (nginx-Fehler sind nur eine Warnung).
smoke_tests() {
    echo
    log "Führe Smoke-Tests durch…"
    local _ healthy=0
    for _ in $(seq 1 15); do
        if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null | grep -q '"ok"'; then
            healthy=1
            break
        fi
        sleep 2
    done

    if systemctl is-active --quiet netprobe; then
        ok "systemd-Dienst läuft."
    else
        warn "Dienst läuft nicht. Details: journalctl -u netprobe -n 50"
    fi
    if [ "$healthy" -eq 1 ]; then
        ok "Backend-Healthcheck (127.0.0.1:8000) erfolgreich."
    else
        warn "Backend-Healthcheck fehlgeschlagen."
    fi
    if curl -fsS --max-time 10 http://127.0.0.1/api/health 2>/dev/null | grep -q '"ok"'; then
        ok "nginx-Proxy-Healthcheck (Port 80) erfolgreich."
    else
        warn "nginx-Proxy-Healthcheck fehlgeschlagen."
    fi
    [ "$healthy" -eq 1 ]
}

_yn() { if [ "$1" -eq 1 ]; then echo "$2"; else echo "$3"; fi; }

# Ergebnis der Änderungserkennung als Tabelle.
print_plan() {
    local mig
    if [ "$MIGRATIONS_PENDING" -eq 1 ]; then
        mig="ausstehend (${MIGRATION_FROM:-leer} -> ${MIGRATION_TO:-head})"
    else
        mig="aktuell (${MIGRATION_TO:-?})"
    fi
    echo
    echo -e "${C_BOLD}  Änderungsanalyse${C_RST}"
    echo "    Installiert:          $(installed_version)"
    echo "    Neu:                  ${NEW_VERSION}"
    echo "    Backend-Code:         $(_yn "$CHANGED_BACKEND" "geändert -> wird eingespielt" "unverändert")"
    echo "    Python-Abhängigkeiten: $(_yn "$CHANGED_DEPS" "geändert -> pip install" "unverändert")"
    echo "    Frontend:             $(_yn "$CHANGED_FRONTEND" \
        "$( [ "$FRONTEND_PREBUILT" -eq 1 ] && echo "geändert -> vorgebautes dist" || echo "geändert -> wird gebaut")" \
        "unverändert")"
    echo "    systemd-Unit:         $(_yn "$CHANGED_UNIT" "geändert" "unverändert")"
    echo "    Datenbank-Migrationen: ${mig}"
    [ "$VENV_BROKEN" -eq 1 ] && echo "    Python-venv:          beschädigt -> wird neu erstellt"
    [ "$OPT_FORCE" -eq 1 ] && echo "    (--force: alle Komponenten werden neu eingespielt)"
    echo
}

print_summary() {
    local kind="$1" ip headline color="$C_OK"
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    case "$kind" in
        install)  headline="netprobe ${NEW_VERSION} wurde installiert." ;;
        update)   headline="netprobe wurde auf ${NEW_VERSION} aktualisiert." ;;
        current)  headline="netprobe ist bereits aktuell – nichts zu tun." ;;
        rollback) headline="netprobe wurde auf den Snapshot zurückgerollt."; color="$C_WARN" ;;
    esac
    echo
    echo -e "${color}==============================================================${C_RST}"
    echo -e "${color} ${headline}${C_RST}"
    echo -e "${color}==============================================================${C_RST}"
    if [ "$kind" = "update" ]; then
        echo "  Eingespielt:"
        [ "$CHANGED_BACKEND" -eq 1 ]    && echo "    - Backend-Code"
        [ "$CHANGED_DEPS" -eq 1 ]       && echo "    - Python-Abhängigkeiten"
        [ "$CHANGED_FRONTEND" -eq 1 ]   && echo "    - Frontend"
        [ "$CHANGED_UNIT" -eq 1 ]       && echo "    - systemd-Unit"
        [ "$MIGRATIONS_PENDING" -eq 1 ] && echo "    - Datenbank-Migrationen (jetzt: ${MIGRATION_TO:-head})"
        [ "$VENV_BROKEN" -eq 1 ]        && echo "    - Python-venv neu erstellt"
    fi
    if [ -n "$SNAPSHOT_DIR" ]; then
        echo "  Snapshot:       ${SNAPSHOT_DIR}"
        echo "  Rollback:       sudo bash ${APP_DIR}/install.sh --rollback"
    fi
    if [ -n "$GENERATED_ADMIN_PASS" ]; then
        echo
        echo -e "${C_BOLD}  Initialer Admin (Passwort wird NUR JETZT angezeigt – bitte sofort ändern):${C_RST}"
        echo "    Benutzer:     ${GENERATED_ADMIN_USER}"
        echo "    Passwort:     ${GENERATED_ADMIN_PASS}"
        echo "    Neu setzen:   cd ${APP_DIR}/backend && sudo -u ${APP_USER} env NETPROBE_ENV_FILE=${ENV_FILE} \\"
        echo "                    ${VENV_DIR}/bin/python -m app.cli create-admin --username ${GENERATED_ADMIN_USER} --force"
        echo
    fi
    echo "  Weboberfläche:  http://${ip:-<server-ip>}/"
    echo "  API-Dokumentation: http://${ip:-<server-ip>}/api/docs"
    echo "  Konfiguration:  ${ENV_FILE}"
    echo "  Dienststatus:   systemctl status netprobe"
    echo "  Logs:           journalctl -u netprobe -f"
    echo "  Update:         sudo bash ${APP_DIR}/install.sh   (lädt den neuesten Stand)"
    echo
    [ "$kind" = "install" ] && echo "  TLS aktivieren: siehe deploy/nginx/netprobe.conf und docs/security.md" && echo
    return 0
}

# ---------------------------------------------------------------------------
# 9. Quelle, Modus-Erkennung
# ---------------------------------------------------------------------------
# Lädt die Quelle von GitHub, wenn das Skript nicht aus einem netprobe-Verzeichnis
# läuft ("curl | bash" oder Aufruf von /opt/netprobe/install.sh).
bootstrap_source() {
    if [ -n "$SRC_DIR" ] && [ "$SRC_DIR" != "$APP_DIR" ] && [ -d "$SRC_DIR/backend/app" ]; then
        return 0
    fi
    command -v curl >/dev/null 2>&1 || die "curl wird benötigt (apt-get install curl)."
    BOOTSTRAP_DIR="$(mktemp -d /tmp/netprobe-install.XXXXXX)"

    local urls=() url found=""
    if [ -n "$NETPROBE_SOURCE_URL" ]; then
        urls=("$NETPROBE_SOURCE_URL")
    elif [ -n "$NETPROBE_REF" ]; then
        urls=("https://codeload.github.com/${NETPROBE_REPO}/tar.gz/${NETPROBE_REF}")
    else
        # Bevorzugt das neueste Release-Paket (Frontend vorgebaut), sonst Branch "main".
        urls=("https://github.com/${NETPROBE_REPO}/releases/latest/download/netprobe.tar.gz"
              "https://codeload.github.com/${NETPROBE_REPO}/tar.gz/main")
    fi
    for url in "${urls[@]}"; do
        log "Lade netprobe: ${url}"
        if curl -fsSL --retry 3 --connect-timeout 15 -o "$BOOTSTRAP_DIR/src.tar.gz" "$url"; then
            found="$url"
            break
        fi
        warn "Nicht verfügbar: ${url}"
    done
    [ -n "$found" ] || die "netprobe konnte nicht geladen werden (NETPROBE_REPO=${NETPROBE_REPO}).
       Repository per NETPROBE_REPO=<owner>/<repo> oder Archiv per NETPROBE_SOURCE_URL=<url> angeben."

    mkdir -p "$BOOTSTRAP_DIR/src"
    tar -xzf "$BOOTSTRAP_DIR/src.tar.gz" -C "$BOOTSTRAP_DIR/src" || die "Archiv ${found} ist beschädigt."
    local inst
    inst="$(find "$BOOTSTRAP_DIR/src" -maxdepth 2 -type f -name install.sh | head -n1)"
    [ -n "$inst" ] || die "Archiv ${found} enthält kein netprobe (install.sh fehlt)."
    SRC_DIR="$(dirname "$inst")"
    ok "Quelle geladen."
}

# Release-Paket (Datei RELEASE + vorgebautes dist) oder Repository-Checkout?
detect_source() {
    [ -d "$SRC_DIR/backend/app" ] || die "Kein netprobe-Quellverzeichnis: ${SRC_DIR}/backend/app fehlt."
    if [ -f "$SRC_DIR/RELEASE" ] && [ -f "$SRC_DIR/frontend/dist/index.html" ]; then
        FRONTEND_PREBUILT=1
        NEW_VERSION="$(head -n1 "$SRC_DIR/RELEASE")"
    elif [ -f "$SRC_DIR/frontend/package.json" ]; then
        FRONTEND_PREBUILT=0
        local ver rev
        ver="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$SRC_DIR/backend/pyproject.toml" | head -n1)"
        rev="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo "${NETPROBE_REF:-src}")"
        NEW_VERSION="${ver:-?} (${rev}, $(date -u +%Y-%m-%d))"
        log "Quellcode-Stand erkannt: Frontend wird bei Änderungen auf dem Server gebaut (Node.js)."
    else
        die "Weder vorgebautes Frontend (Release-Paket) noch Frontend-Quellcode gefunden."
    fi
}

# Installiert = /opt/netprobe mit venv (auch ein beschädigtes venv zählt – es wird repariert).
is_installed() { [ -d "$VENV_DIR" ]; }

installed_version() {
    if [ -f "$STATE_DIR/version" ]; then cat "$STATE_DIR/version"; else echo "unbekannt (ältere Installation)"; fi
}

choose_mode() {
    if [ "$MODE" = "update" ] && ! is_installed; then
        die "Keine bestehende Installation gefunden (${VENV_DIR}) – Update nicht möglich."
    fi
    if [ -n "$MODE" ]; then return; fi
    if is_installed; then
        MODE="update"
        log "Bestehende Installation gefunden ($(installed_version)) -> Smart-Update."
    else
        MODE="install"
        log "Keine Installation unter ${APP_DIR} gefunden -> Neuinstallation von netprobe ${NEW_VERSION}."
    fi
}

# Pfad der SQLite-Datenbank aus der Konfiguration (Default /var/lib/netprobe/db.sqlite).
_db_path() {
    local url
    url="$( ( set -a; . "$ENV_FILE" 2>/dev/null; echo "${NETPROBE_DATABASE_URL:-}" ) )"
    url="${url:-sqlite:///${DATA_DIR}/db.sqlite}"
    echo "${url#sqlite:///}"
}

# ---------------------------------------------------------------------------
# 10. Snapshots (Backup) und Rollback
# ---------------------------------------------------------------------------
# Snapshot = <SNAPSHOT_ROOT>/snapshot_<zeit>/ mit
#   db.sqlite.gz    konsistente DB-Kopie (sqlite3 .backup, WAL-sicher; restore.sh-kompatibel)
#   app.tar.gz      /opt/netprobe ohne venv (Code, dist, Skripte, Installer-Zustand)
#   config.tar.gz   /etc/netprobe, systemd-Units, nginx-Site, logrotate
#   MANIFEST        Version, Zeitpunkt, Anlass
# Argument "noprune" überspringt das Aufräumen alter Snapshots.
backup_current_installation() {
    local ts db db_mb=0 app_mb=0 f cfg=()
    ts="$(date +%Y%m%d_%H%M%S)"
    db="$(_db_path)"
    [ -f "$db" ] && db_mb=$(( $(stat -c %s "$db") / 1048576 ))
    [ -d "$APP_DIR" ] && app_mb="$(du -sm --exclude="$VENV_DIR" "$APP_DIR" 2>/dev/null | cut -f1)"
    require_space "$SNAPSHOT_ROOT" $(( db_mb + ${app_mb:-0} + 100 )) "den Snapshot"

    mkdir -p "$SNAPSHOT_ROOT"
    SNAPSHOT_DIR="${SNAPSHOT_ROOT}/snapshot_${ts}"
    ( umask 077; mkdir -p "$SNAPSHOT_DIR" )
    log "Erstelle Snapshot ${SNAPSHOT_DIR}…"

    if [ -f "$db" ]; then
        command -v sqlite3 >/dev/null 2>&1 || apt-get install -y -qq sqlite3
        ( umask 077; sqlite3 "$db" ".backup '${SNAPSHOT_DIR}/db.sqlite'" ) \
            || die "Datenbank-Backup fehlgeschlagen – Abbruch, es wurde nichts verändert."
        gzip -f "${SNAPSHOT_DIR}/db.sqlite"
    else
        warn "Keine Datenbank unter ${db} – Snapshot ohne DB."
    fi
    if [ -d "$APP_DIR" ]; then
        tar -czf "${SNAPSHOT_DIR}/app.tar.gz" -C / --exclude="${VENV_DIR#/}" "${APP_DIR#/}"
    fi
    for f in "$ETC_DIR" /etc/systemd/system/netprobe.service /etc/systemd/system/netprobe-iperf3.service \
             /etc/nginx/sites-available/netprobe.conf /etc/logrotate.d/netprobe; do
        [ -e "$f" ] && cfg+=("${f#/}")
    done
    if [ "${#cfg[@]}" -gt 0 ]; then
        tar -czf "${SNAPSHOT_DIR}/config.tar.gz" -C / "${cfg[@]}"
    fi
    {
        echo "version=$(installed_version)"
        echo "created=$(date -Is)"
        echo "reason=${MODE:-backup}"
        echo "db=${db}"
    } > "${SNAPSHOT_DIR}/MANIFEST"
    chmod 600 "$SNAPSHOT_DIR"/*
    ok "Snapshot erstellt ($(du -sh "$SNAPSHOT_DIR" | cut -f1))."
    [ "${1:-}" = "noprune" ] || prune_snapshots
}

_snapshots() { find "$SNAPSHOT_ROOT" -maxdepth 1 -type d -name 'snapshot_*' 2>/dev/null | LC_ALL=C sort -r; }

prune_snapshots() {
    local keep="$KEEP_SNAPSHOTS" old
    [[ "$keep" =~ ^[1-9][0-9]*$ ]] || keep=5
    old="$(_snapshots | tail -n +$((keep + 1)))"
    [ -n "$old" ] || return 0
    echo "$old" | xargs -r -d '\n' rm -rf
    ok "Ältere Snapshots entfernt (behalten werden die neuesten ${keep})."
}

list_snapshots() {
    local d n=0
    echo "Snapshots unter ${SNAPSHOT_ROOT} (neueste zuerst):"
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        n=$((n + 1))
        printf "  %-28s %6s  %-8s %s\n" "$(basename "$d")" "$(du -sh "$d" | cut -f1)" \
            "$(sed -n 's/^reason=//p' "$d/MANIFEST" 2>/dev/null)" \
            "$(sed -n 's/^version=//p' "$d/MANIFEST" 2>/dev/null)"
    done <<< "$(_snapshots)"
    [ "$n" -gt 0 ] || echo "  (keine)"
}

# Spielt einen Snapshot zurück: Code, Konfiguration und Datenbank; startet den Dienst.
restore_snapshot() {
    local dir="$1" db tmp
    [ -f "$dir/MANIFEST" ] || die "Kein gültiger Snapshot: ${dir}"
    log "Rolle zurück auf Snapshot ${dir} ($(sed -n 's/^version=//p' "$dir/MANIFEST"))…"
    systemctl stop netprobe 2>/dev/null || true

    if [ -f "$dir/app.tar.gz" ]; then
        rm -rf "$APP_DIR/backend" "$APP_DIR/frontend" "$APP_DIR/scripts" "$STATE_DIR"
        tar -xzf "$dir/app.tar.gz" -C /
        ok "Anwendung wiederhergestellt."
    fi
    if [ -f "$dir/config.tar.gz" ]; then
        tar -xzf "$dir/config.tar.gz" -C /
        systemctl daemon-reload
        ok "Konfiguration wiederhergestellt."
    fi
    if [ -f "$dir/db.sqlite.gz" ]; then
        db="$(_db_path)"
        tmp="$(mktemp "$(dirname "$db")/.netprobe-restore.XXXXXX")"
        gunzip -c "$dir/db.sqlite.gz" > "$tmp"
        if ! sqlite3 "$tmp" "PRAGMA integrity_check;" | grep -qx "ok"; then
            rm -f "$tmp"
            die "Integritätsprüfung der Snapshot-DB fehlgeschlagen – Datenbank NICHT ersetzt."
        fi
        # Alte WAL-/SHM-Dateien dürfen nicht auf die zurückgespielte DB angewendet werden.
        rm -f "${db}-wal" "${db}-shm"
        mv "$tmp" "$db"
        chown "$APP_USER:$APP_GROUP" "$db"
        ok "Datenbank wiederhergestellt."
    fi
    # Das netprobe-Paket im venv auf den zurückgespielten Stand bringen (offline).
    if venv_healthy && "$VENV_DIR/bin/python" -c 'import setuptools' 2>/dev/null; then
        "$VENV_DIR/bin/pip" install --quiet --no-deps --no-build-isolation --force-reinstall \
            "$APP_DIR/backend" || warn "pip-Reinstallation des Pakets fehlgeschlagen."
    fi
    secure_app_dir
    if nginx -t >/dev/null 2>&1; then systemctl reload-or-restart nginx || true; fi
    systemctl start netprobe || warn "Dienst konnte nicht gestartet werden (journalctl -u netprobe -n 50)."
}

# Neuinstallation über eine bestehende Installation (--install): Snapshot, venv neu.
# Daten und Konfiguration bleiben erhalten; Löschen nur interaktiv nach Bestätigung.
prepare_reinstall() {
    is_installed || return 0
    backup_current_installation
    systemctl stop netprobe 2>/dev/null || true
    rm -rf "$VENV_DIR" "$STATE_DIR/deps.sha256" "$STATE_DIR/frontend-src.md5"

    if [ "$INTERACTIVE" -eq 0 ]; then
        ok "Daten und Konfiguration bleiben erhalten."
        return 0
    fi
    echo
    local keep confirm
    read -r -p "Datenbank, Messdaten und Konfiguration BEHALTEN? [J/n]: " keep
    if [[ "${keep,,}" != "n" ]]; then
        ok "Daten und Konfiguration bleiben erhalten."
        return 0
    fi
    warn "Es werden gelöscht: Datenbank, Captures, Reports, ${ENV_FILE} (inkl. SECRET_KEY)."
    warn "Der Snapshot ${SNAPSHOT_DIR} bleibt erhalten."
    read -r -p "Zur Bestätigung LOESCHEN eingeben: " confirm
    if [ "$confirm" != "LOESCHEN" ]; then
        ok "Nicht bestätigt – Daten und Konfiguration bleiben erhalten."
        return 0
    fi
    local db; db="$(_db_path)"
    rm -f "$db" "${db}-wal" "${db}-shm"
    rm -rf "$CAPTURE_DIR" "$DATA_DIR/reports"
    rm -f "$ENV_FILE"
    ok "Daten und Konfiguration gelöscht."
}

# Legt den Installer unter /opt/netprobe ab (für Updates/Rollback ohne Quellverzeichnis).
# Über temporäre Datei + mv (neuer Inode), da das Skript gerade selbst laufen kann.
install_self() {
    [ -f "$SRC_DIR/install.sh" ] || return 0
    cp "$SRC_DIR/install.sh" "$APP_DIR/.install.sh.new"
    chmod 755 "$APP_DIR/.install.sh.new"
    mv -f "$APP_DIR/.install.sh.new" "$APP_DIR/install.sh"
}

# ---------------------------------------------------------------------------
# 11. Ablauf
# ---------------------------------------------------------------------------
# 1 während Änderungen an einer bestehenden Installation laufen: bricht das Skript
# dann ab, rollt on_exit() automatisch auf SNAPSHOT_DIR zurück.
UPDATE_IN_PROGRESS=0

on_exit() {
    local rc=$?
    if [ "$rc" -ne 0 ] && [ "$UPDATE_IN_PROGRESS" -eq 1 ] && [ -n "$SNAPSHOT_DIR" ]; then
        UPDATE_IN_PROGRESS=0
        echo
        warn "Update fehlgeschlagen (Exit-Code ${rc}) – automatischer Rollback auf ${SNAPSHOT_DIR}…"
        if restore_snapshot "$SNAPSHOT_DIR" && smoke_tests; then
            warn "Rollback abgeschlossen; der vorherige Stand läuft wieder. Ursache: siehe Ausgabe oben."
        else
            warn "Rollback fehlgeschlagen oder Dienst antwortet nicht! journalctl -u netprobe -n 50"
            warn "Erneut versuchen: sudo bash ${APP_DIR}/install.sh --rollback=${SNAPSHOT_DIR}"
        fi
    fi
    if [ -n "$BOOTSTRAP_DIR" ]; then rm -rf "$BOOTSTRAP_DIR"; fi
    exit "$rc"
}

setup_fresh_installation() {
    STEPS_TOTAL=11
    [ "$INTERACTIVE" -eq 1 ] && STEPS_TOTAL=12
    # Bei einer Neuinstallation werden alle Komponenten eingespielt.
    CHANGED_BACKEND=1; CHANGED_DEPS=1; CHANGED_FRONTEND=1; CHANGED_UNIT=1; MIGRATIONS_PENDING=1

    step "Vorbedingungen prüfen";            check_prereqs
    prepare_reinstall
    step "Systempakete installieren";        install_packages
    step "Benutzer und Verzeichnisse";       setup_user_dirs; seed_speedtest_license
    step "Konfiguration";                    setup_env
    step "Backend und Python-Umgebung";      deploy_backend; install_python_deps
    step "Frontend";                         deploy_frontend
    step "Datenbank initialisieren (Alembic)"
    run_migrations || die "Datenbankmigration fehlgeschlagen. Details siehe oben."
    step "Admin-Benutzer";                   setup_admin
    step "systemd-Dienste";                  setup_systemd; setup_iperf3_service
    step "nginx, logrotate, GeoIP, Firewall"; setup_geoip; setup_tls; setup_nginx; setup_logrotate; setup_ufw
    if [ "$INTERACTIVE" -eq 1 ]; then
        step "Netzwerk";                     configure_network
    fi
    step "Smoke-Tests"
    smoke_tests || warn "Smoke-Tests nicht vollständig erfolgreich – siehe Hinweise oben."
    record_version
    install_self
    print_summary install
}

# Analysiert die bestehende Installation und setzt die CHANGED_*-Flags.
plan_update() {
    log "Analysiere bestehende Installation…"
    if ! venv_healthy; then VENV_BROKEN=1; fi
    if backend_code_changed; then CHANGED_BACKEND=1; fi
    if python_deps_changed; then CHANGED_DEPS=1; fi
    if frontend_code_changed; then CHANGED_FRONTEND=1; fi
    if systemd_unit_changed; then CHANGED_UNIT=1; fi
    if [ "$VENV_BROKEN" -eq 1 ]; then
        MIGRATIONS_PENDING=1              # ohne venv nicht prüfbar -> nach Reparatur migrieren
    elif migrations_pending "$SRC_DIR/backend"; then
        MIGRATIONS_PENDING=1
    fi
    if [ "$OPT_FORCE" -eq 1 ]; then CHANGED_BACKEND=1; CHANGED_FRONTEND=1; CHANGED_UNIT=1; fi
    if [ "$OPT_REBUILD_FRONTEND" -eq 1 ]; then CHANGED_FRONTEND=1; fi
    if [ "$OPT_RUN_MIGRATIONS" -eq 1 ]; then MIGRATIONS_PENDING=1; fi
    print_plan
}

# Nichts zu aktualisieren: nur fehlende/abweichende Teile reparieren, ohne Neustart.
self_heal() {
    log "Prüfe Installation auf fehlende Teile…"
    setup_user_dirs
    seed_speedtest_license
    setup_env
    setup_tls
    setup_nginx
    setup_logrotate
    if [ "$(sha256sum < "$SRC_DIR/deploy/systemd/netprobe-iperf3.service")" \
            != "$(sha256sum < /etc/systemd/system/netprobe-iperf3.service 2>/dev/null)" ]; then
        setup_iperf3_service
    fi
    systemctl enable netprobe >/dev/null 2>&1 || true
    if ! systemctl is-active --quiet netprobe; then
        warn "Dienst netprobe lief nicht – wird gestartet."
        systemctl start netprobe || true
    fi
    setup_admin
}

perform_smart_update() {
    check_prereqs
    plan_update

    install_packages          # nur fehlende Pakete; Node.js nur, wenn gebaut werden muss
    local pending=$(( CHANGED_BACKEND | CHANGED_DEPS | CHANGED_FRONTEND | CHANGED_UNIT \
                      | MIGRATIONS_PENDING | VENV_BROKEN ))
    if [ "$pending" -eq 0 ]; then
        ok "Code, Frontend, Abhängigkeiten und Datenbankschema sind aktuell."
        self_heal
        smoke_tests || warn "Smoke-Tests nicht vollständig erfolgreich – siehe Hinweise oben."
        record_version
        install_self
        print_summary current
        return 0
    fi

    backup_current_installation
    UPDATE_IN_PROGRESS=1      # ab hier: Abbruch -> automatischer Rollback (on_exit)

    log "Stoppe Dienst netprobe…"
    systemctl stop netprobe 2>/dev/null || true
    setup_user_dirs
    seed_speedtest_license
    setup_env

    [ "$CHANGED_BACKEND" -eq 1 ] && deploy_backend
    if [ $(( CHANGED_BACKEND | CHANGED_DEPS | VENV_BROKEN )) -eq 1 ]; then
        install_python_deps
    fi
    [ "$CHANGED_FRONTEND" -eq 1 ] && deploy_frontend

    # Nach dem Einspielen erneut gegen die NEUEN heads prüfen.
    if [ "$MIGRATIONS_PENDING" -eq 1 ] || migrations_pending "$APP_DIR/backend"; then
        MIGRATIONS_PENDING=1
        run_migrations || die "Datenbankmigration fehlgeschlagen bzw. nicht verifiziert."
    fi

    setup_systemd             # aktualisiert die Unit und startet den Dienst
    setup_iperf3_service
    setup_tls
    setup_nginx
    setup_logrotate
    setup_admin
    smoke_tests || die "Backend antwortet nach dem Update nicht."
    UPDATE_IN_PROGRESS=0

    record_version
    install_self
    print_summary update
}

perform_rollback() {
    is_installed || die "Keine Installation unter ${APP_DIR} gefunden."
    local dir="$OPT_ROLLBACK"
    if [ "$dir" = "latest" ]; then
        # Neuester Snapshot, der NICHT selbst von einem Rollback angelegt wurde – sonst
        # würde ein zweites --rollback nur den ersten rückgängig machen.
        dir=""
        local d
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            grep -qx "reason=rollback" "$d/MANIFEST" 2>/dev/null && continue
            dir="$d"
            break
        done <<< "$(_snapshots)"
        [ -n "$dir" ] || die "Kein Snapshot unter ${SNAPSHOT_ROOT} vorhanden (siehe --list-backups)."
    elif [ ! -d "$dir" ] && [ -d "${SNAPSHOT_ROOT}/${dir}" ]; then
        dir="${SNAPSHOT_ROOT}/${dir}"
    fi
    [ -f "$dir/MANIFEST" ] || die "Kein gültiger Snapshot: ${dir} (siehe --list-backups)."
    MODE="rollback"
    # Aktuellen Stand vorher sichern, damit auch der Rollback umkehrbar ist. Nicht
    # aufräumen, sonst könnte der Ziel-Snapshot selbst gelöscht werden.
    local target="$dir"
    backup_current_installation noprune
    restore_snapshot "$target"
    smoke_tests || warn "Smoke-Tests nicht vollständig erfolgreich – siehe Hinweise oben."
    print_summary rollback
    echo "  Zurückgerollt auf:   ${target}"
    echo "  Stand vor Rollback:  ${SNAPSHOT_DIR}"
    echo
}

usage() {
    cat <<'EOF'
netprobe Installer/Updater – erkennt Neuinstallation vs. Update automatisch.

  curl -sSL https://raw.githubusercontent.com/mwieschen/netprobe/main/install.sh | sudo bash
  sudo bash install.sh [optionen]
  sudo bash /opt/netprobe/install.sh [optionen]     # Update vom neuesten GitHub-Stand

Optionen (bei "curl | bash": ... | sudo bash -s -- <optionen>):
  --rebuild-frontend   Frontend erzwingen (neu bauen bzw. dist neu einspielen)
  --run-migrations     alembic upgrade head erzwingen (inkl. Prüfung)
  --backup-only        nur Snapshot anlegen, sonst nichts ändern
  --rollback[=DIR]     auf den neuesten (oder angegebenen) Snapshot zurückrollen
  --list-backups       vorhandene Snapshots anzeigen
  --force              alle Komponenten neu einspielen (Update)
  --update             Update erzwingen (Fehler, wenn nichts installiert ist)
  --install            vollständige Neueinrichtung über eine bestehende Installation
  --interactive        Admin-Passwort, Firewall und Netzwerk interaktiv abfragen
  -h, --help           diese Hilfe

Umgebungsvariablen: NETPROBE_REPO, NETPROBE_REF, NETPROBE_SOURCE_URL,
NETPROBE_ADMIN_USER, NETPROBE_ADMIN_PASSWORD, NETPROBE_KEEP_SNAPSHOTS.
Details: docs/installation.md und docs/update.md
EOF
}

main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --rebuild-frontend) OPT_REBUILD_FRONTEND=1 ;;
            --run-migrations)   OPT_RUN_MIGRATIONS=1 ;;
            --backup-only)      OPT_BACKUP_ONLY=1 ;;
            --rollback)         OPT_ROLLBACK="latest" ;;
            --rollback=*)       OPT_ROLLBACK="${1#--rollback=}"; [ -n "$OPT_ROLLBACK" ] || OPT_ROLLBACK="latest" ;;
            --list-backups)     OPT_LIST_BACKUPS=1 ;;
            --force)            OPT_FORCE=1 ;;
            --update)           MODE="update" ;;
            --install)          MODE="install" ;;
            --interactive)      INTERACTIVE=1 ;;
            -h|--help)          usage; exit 0 ;;
            *) die "Unbekannte Option: $1 (siehe --help)" ;;
        esac
        shift
    done

    [ "$(id -u)" -eq 0 ] || die "Bitte als root ausführen (sudo bash install.sh)."

    # Bei "curl | bash" ist stdin das Skript selbst: Kindprozesse (apt, pip, npm …)
    # dürfen es nicht lesen. Rückfragen gibt es nur mit --interactive (über das Terminal).
    if [ "$INTERACTIVE" -eq 1 ]; then
        if [ ! -t 0 ]; then
            if ( exec </dev/tty ) 2>/dev/null; then exec </dev/tty; else die "--interactive benötigt ein Terminal."; fi
        fi
    else
        exec </dev/null
    fi
    trap on_exit EXIT

    if [ "$OPT_LIST_BACKUPS" -eq 1 ]; then list_snapshots; exit 0; fi
    if [ -n "$OPT_ROLLBACK" ]; then perform_rollback; exit 0; fi
    if [ "$OPT_BACKUP_ONLY" -eq 1 ]; then
        is_installed || die "Keine Installation unter ${APP_DIR} gefunden – nichts zu sichern."
        MODE="backup"
        backup_current_installation
        ok "Snapshot: ${SNAPSHOT_DIR}"
        exit 0
    fi

    bootstrap_source
    detect_source
    choose_mode
    log "Modus: $([ "$MODE" = "update" ] && echo "Smart-Update" || echo "Neuinstallation")"

    if [ "$MODE" = "update" ]; then perform_smart_update; else setup_fresh_installation; fi
}

# Aufruf und exit in EINER Zeile: Bash hat damit das gesamte Skript gelesen, bevor
# stdin umgelenkt wird (wichtig für "curl | bash").
main "$@"; exit
