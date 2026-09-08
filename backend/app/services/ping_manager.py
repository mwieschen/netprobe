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
    return [settings.ping_bin, "-n", "-c", "1", "-W", str(timeout_s), host]


class PingManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Merkt sich das Haupt-Event-Loop, damit Jobs auch aus Threadpool-
        Kontext (synchrone Endpunkte) sicher gestartet werden können."""
        self._loop = loop

    async def _ping_once(self, host: str) -> float | None:
        try:
            proc = await run_async(_build_single_ping_args(host), timeout=5)
        except ToolNotFoundError as exc:
            log.error("Ping-Tool fehlt: %s", exc)
            return None
        if proc.returncode != 0:
            return None
        return parse_ping_single(proc.stdout)

    async def _run_job(self, job_id: int, host: str, interval_s: int) -> None:
        interval_s = max(1, interval_s)
        log.info("Dauerping-Job %s gestartet (%s, alle %ss)", job_id, host, interval_s)
        try:
            while True:
                rtt = await self._ping_once(host)
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
