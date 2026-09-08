"""WAN-Qualitätsmessung via fping (bevorzugt) oder ping."""

from __future__ import annotations

import shutil

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.wan import WanResult, WanTarget
from app.services.parsers import RttStats, parse_fping_output, parse_ping_output
from app.services.proc import ToolNotFoundError, run_sync

log = get_logger("netprobe.wan")


def _fping_available() -> bool:
    return shutil.which(settings.fping_bin) is not None


def _validate_host(host: str) -> str:
    # Verhindert Argument-Injection: ein mit "-" beginnender Host würde sonst als Flag gelesen.
    if host.startswith("-"):
        raise ValueError(f"Ungültiger Host: {host!r}")
    return host


def build_fping_args(host: str, count: int, timeout_ms: int) -> list[str]:
    # -q: quiet, -C n: n Pings mit Einzelwerten, -p: Periode, -t: Timeout pro Ping
    return [
        settings.fping_bin,
        "-q",
        "-C",
        str(count),
        "-p",
        "200",
        "-t",
        str(timeout_ms),
        "--",
        _validate_host(host),
    ]


def build_ping_args(host: str, count: int, timeout_ms: int) -> list[str]:
    timeout_s = max(1, round(timeout_ms / 1000))
    return [
        settings.ping_bin,
        "-n",  # keine DNS-Auflösung der Antworten
        "-c",
        str(count),
        "-W",
        str(timeout_s),
        "--",
        _validate_host(host),
    ]


def measure(host: str, count: int, timeout_ms: int) -> tuple[RttStats | None, str | None]:
    """Führt eine Messung durch. Gibt (Statistik, Fehler) zurück."""
    try:
        if _fping_available():
            proc = run_sync(build_fping_args(host, count, timeout_ms), timeout=count * 2 + 10)
            stats = parse_fping_output(proc.stderr, count)
        else:
            proc = run_sync(build_ping_args(host, count, timeout_ms), timeout=count * 2 + 10)
            stats = parse_ping_output(proc.stdout, count)
    except ToolNotFoundError as exc:
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)

    if not stats.rtts and proc.returncode not in (0, 1):
        # returncode 1 bei fping/ping = einige/alle Timeouts, weiterhin gültig
        err = proc.stderr.strip() or f"Exit-Code {proc.returncode}"
        return stats, err
    return stats, None


def run_target(db: Session, target: WanTarget) -> WanResult:
    stats, error = measure(target.host, target.count, target.timeout_ms)

    if stats is None:
        result = WanResult(target_id=target.id, ok=False, error=error)
    else:
        ok = bool(stats.rtts) and stats.loss_pct < 100.0
        result = WanResult(
            target_id=target.id,
            ok=ok,
            loss_pct=stats.loss_pct,
            rtt_min=stats.rtt_min,
            rtt_avg=stats.rtt_avg,
            rtt_max=stats.rtt_max,
            rtt_stddev=stats.rtt_stddev,
            jitter_ms=stats.jitter_ms,
            error=error if not ok else None,
        )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
