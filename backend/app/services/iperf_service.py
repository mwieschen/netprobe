"""iperf3-Integration: Client-Tests (``iperf3 -c … -J``) und ein vom Backend
verwalteter iperf3-Server-Subprozess (ein/aus, Port).

Sicherheit:
- Ausschließlich Argumentlisten, niemals ``shell=True``.
- Der Zielhost wird validiert (kein führendes ``-``, nur Host-/IP-Zeichen).
- Alles läuft als Benutzer ``netprobe`` ohne Root-Rechte.
"""

from __future__ import annotations

import asyncio
import contextlib
import re

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.iperf import IperfResult
from app.services import kvstore
from app.services.parsers import parse_iperf3_json
from app.services.proc import ToolNotFoundError, ensure_tool, run_sync

log = get_logger("netprobe.iperf3")

SERVER_ENABLED_KEY = "iperf3_server_enabled"
SERVER_PORT_KEY = "iperf3_server_port"

_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


def _validate_host(host: str) -> str:
    host = host.strip()
    if host.startswith("-") or not _HOST_RE.match(host):
        raise ValueError(f"Ungültiger Host: {host!r}")
    return host


def build_client_args(
    host: str, port: int, duration: int, parallel: int, udp: bool, reverse: bool
) -> list[str]:
    args = [
        settings.iperf3_bin,
        "-J",
        "-p",
        str(port),
        "-t",
        str(duration),
        "-P",
        str(parallel),
    ]
    if udp:
        args.append("-u")
    if reverse:
        args.append("-R")
    # -c erwartet den Host als Wert -> ein führendes "-" würde nicht als Flag
    # interpretiert; zusätzlich validieren wir den Host.
    args += ["-c", _validate_host(host)]
    return args


def run_client(
    db: Session,
    *,
    host: str,
    port: int = 5201,
    duration: int = 10,
    parallel: int = 1,
    udp: bool = False,
    reverse: bool = False,
) -> IperfResult:
    """Führt einen iperf3-Client-Test aus und persistiert das Ergebnis."""
    protocol = "udp" if udp else "tcp"
    common = {
        "host": host,
        "port": port,
        "protocol": protocol,
        "duration_s": duration,
        "parallel": parallel,
        "reverse": reverse,
    }

    try:
        args = build_client_args(host, port, duration, parallel, udp, reverse)
    except ValueError as exc:
        return _persist(db, IperfResult(ok=False, error=str(exc), **common))

    try:
        proc = run_sync(args, timeout=duration + 20)
    except ToolNotFoundError as exc:
        return _persist(db, IperfResult(ok=False, error=str(exc), **common))

    parsed = parse_iperf3_json(proc.stdout)
    if not parsed.ok and proc.returncode != 0 and not proc.stdout.strip():
        parsed.error = proc.stderr.strip() or f"Exit-Code {proc.returncode}"

    result = IperfResult(
        ok=parsed.ok,
        sent_mbps=parsed.sent_mbps,
        recv_mbps=parsed.recv_mbps,
        jitter_ms=parsed.jitter_ms,
        lost_percent=parsed.lost_percent,
        lost_packets=parsed.lost_packets,
        packets=parsed.packets,
        out_of_order=parsed.out_of_order,
        retransmits=parsed.retransmits,
        error=parsed.error,
        **common,
    )
    log.info("iperf3 %s ok=%s sent=%s recv=%s", host, parsed.ok, parsed.sent_mbps, parsed.recv_mbps)
    return _persist(db, result)


def _persist(db: Session, result: IperfResult) -> IperfResult:
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


class Iperf3ServerManager:
    """Verwaltet einen einzelnen ``iperf3 -s``-Subprozess (kein systemd nötig)."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._port: int = settings.iperf3_server_port

    @property
    def port(self) -> int:
        return self._port

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _spawn(self) -> None:
        ensure_tool(settings.iperf3_bin)
        self._proc = await asyncio.create_subprocess_exec(
            settings.iperf3_bin,
            "-s",
            "-p",
            str(self._port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("iperf3-Server gestartet auf Port %d", self._port)

    async def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def apply(self, db: Session, *, enabled: bool, port: int | None) -> None:
        """Setzt den gewünschten Zielzustand und persistiert ihn."""
        target_port = int(port) if port else self._port
        if not (1 <= target_port <= 65535):
            raise ValueError("Port muss zwischen 1 und 65535 liegen.")

        if not enabled:
            await self._kill()
        else:
            if self.is_running() and target_port != self._port:
                await self._kill()
            self._port = target_port
            if not self.is_running():
                await self._spawn()

        kvstore.set_value(db, SERVER_ENABLED_KEY, enabled)
        kvstore.set_value(db, SERVER_PORT_KEY, self._port)
        db.commit()

    def resume(self) -> None:
        """Beim Dienststart den zuletzt gewünschten Zustand wiederherstellen."""
        from app.db.session import session_scope

        with session_scope() as db:
            enabled = bool(kvstore.get_value(db, SERVER_ENABLED_KEY, False))
            port = int(kvstore.get_value(db, SERVER_PORT_KEY, settings.iperf3_server_port))
        self._port = port
        if enabled:
            asyncio.ensure_future(self._spawn())

    async def shutdown(self) -> None:
        """Prozess beenden, den persistierten Wunschzustand aber NICHT ändern."""
        await self._kill()


iperf3_server = Iperf3ServerManager()
