"""Pfad-MTU-Ermittlung zu einem Ziel per ``ping -M do`` und binärer Suche.

Getestet wird die IPv4-Pfad-MTU: für eine Kandidaten-MTU ``m`` wird ein Ping mit
``-s (m - 28)`` (20 Byte IP-Header + 8 Byte ICMP-Header) und gesetztem
Don't-Fragment-Bit gesendet. Kommt eine Antwort, passt die MTU.

Sicherheit: Argumentliste, ``--``-Trenner, Ziel wird validiert; läuft ohne Root
(``ping`` erhält ``CAP_NET_RAW`` über den Dienst).
"""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.logging import get_logger
from app.services.proc import ToolNotFoundError, run_sync

log = get_logger("netprobe.mtu")

# IPv4-Header (20) + ICMP-Echo-Header (8)
_IP_ICMP_OVERHEAD = 28
_TARGET_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


def _validate_target(target: str) -> str:
    target = target.strip()
    if target.startswith("-") or not _TARGET_RE.match(target):
        raise ValueError(f"Ungültiges Ziel: {target!r}")
    return target


def _build_probe_args(target: str, payload: int, timeout_s: int) -> list[str]:
    return [
        settings.ping_bin,
        "-4",
        "-M",
        "do",  # Don't Fragment erzwingen
        "-s",
        str(payload),
        "-c",
        "1",
        "-W",
        str(timeout_s),
        "--",
        target,
    ]


def _probe(target: str, mtu: int, timeout_s: int = 2) -> bool:
    payload = mtu - _IP_ICMP_OVERHEAD
    if payload < 0:
        return False
    proc = run_sync(_build_probe_args(target, payload, timeout_s), timeout=timeout_s + 3)
    return proc.returncode == 0


def discover_path_mtu(target: str, min_mtu: int, max_mtu: int) -> dict:
    """Ermittelt die Pfad-MTU. Rückgabe:
    ``{ok, path_mtu, min_mtu, max_mtu, probes: [{mtu, ok}], error}``."""
    target = _validate_target(target)
    lo = max(int(min_mtu), settings.mtu_min)
    hi = min(int(max_mtu), settings.mtu_max)

    probes: list[dict] = []

    def probe(m: int) -> bool:
        ok = _probe(target, m)
        probes.append({"mtu": m, "ok": ok})
        return ok

    if lo >= hi:
        return _result(target, lo, hi, probes, None, "min_mtu muss kleiner als max_mtu sein.")

    try:
        if not probe(lo):
            return _result(
                target, lo, hi, probes, None,
                f"Ziel bei MTU {lo} nicht erreichbar (DF gesetzt) – ICMP evtl. gefiltert "
                f"oder Host down.",
            )
        if probe(hi):
            return _result(target, lo, hi, probes, hi, None)

        # größte funktionierende MTU im offenen Intervall (lo, hi) suchen
        best, a, b = lo, lo, hi
        while b - a > 1:
            mid = (a + b) // 2
            if probe(mid):
                a, best = mid, mid
            else:
                b = mid
        return _result(target, lo, hi, probes, best, None)
    except ToolNotFoundError as exc:
        return _result(target, lo, hi, probes, None, str(exc))


def _result(
    target: str, lo: int, hi: int, probes: list[dict], path_mtu: int | None, error: str | None
) -> dict:
    ok = path_mtu is not None
    if ok:
        log.info("Pfad-MTU zu %s: %d (%d Proben)", target, path_mtu, len(probes))
    return {
        "target": target,
        "ok": ok,
        "path_mtu": path_mtu,
        "min_mtu": lo,
        "max_mtu": hi,
        "probe_count": len(probes),
        "probes": probes,
        "error": error,
    }
