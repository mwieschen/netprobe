#!/usr/bin/env bash
#
# Baut ein schlankes netprobe-Release-Paket (tar.gz, ca. 1–2 MB) auf dem
# Entwicklungsrechner (Linux, macOS oder Windows/Git Bash).
#
# Enthalten: install.sh, ANLEITUNG.md, Backend-Quellcode (ohne Tests/venv/Caches), Alembic-
# Migrationen, VORGEBAUTES Frontend (frontend/dist), deploy/, Betriebsskripte, docs/ und das
# unveränderte Wachtl-Setup für die Windows-PCs (clients/wachtl/, aus github.com/fkroczak/Wachtl,
# MIT-Lizenz; immer die neueste Version unter releases/, Prüfsumme gegen SHA256SUMS.txt geprüft).
# NICHT enthalten: node_modules, .venv, .git, Tests, Frontend-Quellcode.
#
# Auf dem Server wird dadurch weder Node.js noch ein Frontend-Build benötigt.
#
# Verwendung:
#   bash scripts/build-release.sh               # Frontend bauen + Paket erzeugen
#   bash scripts/build-release.sh --skip-build  # vorhandenes frontend/dist verwenden
#   WACHTL_VERSION=0.9.6 bash scripts/build-release.sh   # bestimmte Wachtl-Version statt der neuesten
#
# Ergebnis: release/netprobe-<version>-<commit>.tar.gz
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    esac
done

version="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$ROOT/backend/pyproject.toml" | head -n1)"
rev="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [ "$rev" != "nogit" ] && [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    rev="${rev}-dirty"
fi
name="netprobe-${version:-0.0.0}-${rev}"

# 1. Frontend bauen
if [ "$SKIP_BUILD" -eq 0 ]; then
    echo "[release] Baue Frontend…"
    (
        cd "$ROOT/frontend"
        if [ ! -d node_modules ]; then npm ci; fi
        npm run build
    )
fi
[ -f "$ROOT/frontend/dist/index.html" ] \
    || { echo "frontend/dist fehlt – ohne --skip-build ausführen." >&2; exit 1; }

# 2. Wachtl-Setup holen (Cache: release/wachtl/<Version>/)
WACHTL_REPO="${WACHTL_REPO:-fkroczak/Wachtl}"
wachtl_api="https://api.github.com/repos/${WACHTL_REPO}/contents/releases"
wachtl_raw="https://raw.githubusercontent.com/${WACHTL_REPO}/main/releases"
if [ -z "${WACHTL_VERSION:-}" ]; then
    echo "[release] Ermittle neueste Wachtl-Version…"
    WACHTL_VERSION="$(curl -fsSL "$wachtl_api" | sed -n 's/.*"name": *"\([0-9][0-9.]*\)".*/\1/p' \
        | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)"
    [ -n "$WACHTL_VERSION" ] || { echo "Wachtl-Version nicht ermittelbar (${wachtl_api})." >&2; exit 1; }
fi
wachtl_cache="$ROOT/release/wachtl/${WACHTL_VERSION}"
wachtl_exe="WachtlSetup-${WACHTL_VERSION}.exe"
mkdir -p "$wachtl_cache"
for f in SHA256SUMS.txt LIESMICH.md "$wachtl_exe"; do
    if [ ! -s "$wachtl_cache/$f" ] || [ "$f" = SHA256SUMS.txt ]; then
        echo "[release] Lade Wachtl ${WACHTL_VERSION}: ${f}"
        curl -fsSL --retry 3 -o "$wachtl_cache/$f.part" "${wachtl_raw}/${WACHTL_VERSION}/${f}"
        mv "$wachtl_cache/$f.part" "$wachtl_cache/$f"
    fi
done
curl -fsSL --retry 3 -o "$wachtl_cache/LICENSE" "https://raw.githubusercontent.com/${WACHTL_REPO}/main/LICENSE"
expected="$(awk -v f="$wachtl_exe" '{n=$2; sub(/^\*/, "", n)} n==f {print tolower($1)}' "$wachtl_cache/SHA256SUMS.txt")"
actual="$(sha256sum "$wachtl_cache/$wachtl_exe" | cut -d' ' -f1)"
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    rm -f "$wachtl_cache/$wachtl_exe"
    echo "Prüfsumme von ${wachtl_exe} stimmt nicht (erwartet ${expected:-?}, ist ${actual}) – Abbruch." >&2
    exit 1
fi
echo "[release] Wachtl ${WACHTL_VERSION}: Prüfsumme OK (${actual})."

# 3. Paketinhalt zusammenstellen
stage_root="$(mktemp -d)"
trap 'rm -rf "$stage_root"' EXIT
stage="$stage_root/$name"
mkdir -p "$stage/backend" "$stage/frontend" "$stage/scripts"

cp "$ROOT/install.sh" "$ROOT/.env.example" "$ROOT/LICENSE" "$ROOT/README.md" "$ROOT/ANLEITUNG.md" "$stage/"
cp -r "$ROOT/backend/app" "$ROOT/backend/alembic" "$stage/backend/"
cp "$ROOT/backend/alembic.ini" "$ROOT/backend/pyproject.toml" "$stage/backend/"
cp -r "$ROOT/frontend/dist" "$stage/frontend/"
cp -r "$ROOT/deploy" "$ROOT/docs" "$stage/"
cp "$ROOT/scripts/backup.sh" "$ROOT/scripts/restore.sh" "$ROOT/scripts/fix-permissions.sh" "$stage/scripts/"
mkdir -p "$stage/clients/wachtl"
cp "$wachtl_cache"/{"$wachtl_exe",SHA256SUMS.txt,LIESMICH.md,LICENSE} "$stage/clients/wachtl/"
echo "$WACHTL_VERSION" > "$stage/clients/wachtl/VERSION"

find "$stage" -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
find "$stage" -type f \( -name '*.pyc' -o -name '*.sqlite*' \) -delete

# Marker: install.sh erkennt daran das Release-Paket (vorgebautes Frontend).
echo "${version:-0.0.0} (${rev}, $(date -u +%Y-%m-%d), Wachtl ${WACHTL_VERSION})" > "$stage/RELEASE"

# 4. Archiv erzeugen. Shell-Skripte explizit ausführbar (unter Windows fehlt das Bit).
mkdir -p "$ROOT/release"
out="$ROOT/release/${name}.tar.gz"
tar -czf "$out" -C "$stage_root" \
    --owner=0 --group=0 --mode='u+rwX,go+rX,go-w' "$name" 2>/dev/null \
    || tar -czf "$out" -C "$stage_root" "$name"

# Kopie mit festem Namen: als Asset "netprobe.tar.gz" an ein GitHub-Release hängen,
# dann lädt "curl … install.sh | sudo bash" dieses Paket (kein Node.js auf dem Server).
cp "$out" "$ROOT/release/netprobe.tar.gz"

size="$(du -h "$out" | cut -f1)"
echo
echo "[release] Paket erstellt: ${out} (${size})"
echo "[release] Enthält Wachtl-Setup ${WACHTL_VERSION} (clients/wachtl/${wachtl_exe})"
echo "[release] GitHub-Release-Asset: release/netprobe.tar.gz"
echo "          (z. B. gh release create v${version:-0.0.0} release/netprobe.tar.gz)"
echo
echo "Auf den Server übertragen und installieren/aktualisieren:"
echo "  scp \"release/${name}.tar.gz\" <user>@<server>:"
echo "  ssh <user>@<server>"
echo "  tar xzf ${name}.tar.gz && cd ${name} && sudo bash install.sh"
