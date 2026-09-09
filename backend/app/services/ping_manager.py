"""Verwaltung paralleler Dauerping-Jobs als asyncio-Tasks.

Jeder laufende Job pingt in ``interval_s``-Abständen ein Ziel und schreibt ein
``PingSample``. Live-Updates werden über den :data:`ping_broker` publiziert.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.ping import PingJob, PingSample
from app.services.events import ping_broker
from app.services.parsers import parse_ping_single
from app.services.proc import ToolNotFoundError, run_async

log = get_logger("netprobe.ping")


def _build_single_ping_args(host: str, timeout_s: int = 2) -> list[str]:
    if host.startswith("-"):
        raise ValueError(f"Ungültiger Host: {host!r}")
    # "--" trennt Optionen von Operanden, damit ein Host nicht als Flag interpretiert wird.
    return [settings.ping_bin, "-n", "-c", "1", "-W", str(timeout_s), "--", host]


class PingManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # Merkt sich, für welche Jobs ein Ping-Fehler bereits geloggt wurde,
        # damit das Log bei dauerhaften Problemen nicht überläuft.
        self._warned: set[int] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Merkt sich das Haupt-Event-Loop, damit Jobs auch aus Threadpool-
        Kontext (synchrone Endpunkte) sicher gestartet werden können."""
        self._loop = loop

    async def _ping_once(self, host: str, job_id: int | None = None) -> float | None:
        try:
            proc = await run_async(_build_single_ping_args(host), timeout=5)
        except ToolNotFoundError as exc:
            log.error("Ping-Tool fehlt: %s", exc)
            return None
        except ValueError as exc:
            log.error("Ungültiger Ping-Host: %s", exc)
            return None
        if proc.returncode != 0:
            # returncode 1 = Ziel nicht erreichbar / Timeout (normaler Paketverlust).
            # Andere Codes bzw. Meldungen auf stderr deuten auf ein Konfigurationsproblem
            # hin (fehlendes CAP_NET_RAW: "Operation not permitted"). Einmalig pro Job loggen.
            err = proc.stderr.strip()
            if job_id is not None and job_id not in self._warned and (proc.returncode != 1 or err):
                self._warned.add(job_id)
                log.warning(
                    "Dauerping-Job %s: Ping fehlgeschlagen (rc=%s) %s",
                    job_id,
                    proc.returncode,
                    err or "(keine Ausgabe)",
                )
                if "not permitted" in err.lower():
                    log.warning(
                        "Hinweis: 'ping' benötigt CAP_NET_RAW. Bitte AmbientCapabilities in "
                        "der systemd-Unit prüfen bzw. scripts/fix-permissions.sh ausführen."
                    )
            return None
        if job_id is not None:
            self._warned.discard(job_id)
        return parse_ping_single(proc.stdout)

    async def _run_job(self, job_id: int, host: str, interval_s: int) -> None:
        interval_s = max(1, interval_s)
        log.info("Dauerping-Job %s gestartet (%s, alle %ss)", job_id, host, interval_s)
        try:
            while True:
                rtt = await self._ping_once(host, job_id)
                ts = utcnow()
                ok = rtt is not None
                with session_scope() as db:
                    db.add(PingSample(job_id=job_id, ts=ts, rtt_ms=rtt, ok=ok))
                await ping_broker.publish(
                    "ping",
                    {
                        "job_id": job_id,
                        "ts": ts.isoformat(),
                        "rtt_ms": rtt,
                        "ok": ok,
                    },
                )
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            log.info("Dauerping-Job %s gestoppt", job_id)
            raise

    def is_running(self, job_id: int) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    def _create_task(self, job_id: int, host: str, interval_s: int) -> None:
        task = asyncio.ensure_future(self._run_job(job_id, host, interval_s))
        self._tasks[job_id] = task

    def start(self, job: PingJob) -> None:
        if self.is_running(job.id):
            return
        job_id, host, interval_s = job.id, job.host, job.interval_s
        try:
            asyncio.get_running_loop()
            self._create_task(job_id, host, interval_s)
        except RuntimeError:
            # Aus Threadpool-Kontext aufgerufen: auf dem Haupt-Loop einplanen.
            if self._loop is None:
                log.error("Kein Event-Loop gebunden; Job %s nicht gestartet", job_id)
                return
            self._loop.call_soon_threadsafe(self._create_task, job_id, host, interval_s)

    def stop(self, job_id: int) -> None:
        self._warned.discard(job_id)
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def resume_all(self) -> None:
        """Beim Start alle als 'running' markierten Jobs wieder aufnehmen."""
        with session_scope() as db:
            jobs = db.query(PingJob).filter(PingJob.status == "running").all()
            for job in jobs:
                self.start(job)


ping_manager = PingManager()
