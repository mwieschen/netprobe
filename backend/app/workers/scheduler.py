"""APScheduler-basierter Hintergrund-Scheduler (läuft im API-Prozess).

Aufgaben:
- Geplante Speedtests (konfigurierbares Intervall, mit Job-Lock).
- Geplante WAN-Messungen je aktiviertem Ziel.
- 5-Minuten-Aggregation der Dauerpings.
- Retention-Cleanup.
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.wan import WanTarget
from app.services import kvstore, speedtest_service, wan_service
from app.workers.aggregation import aggregate_ping_samples, cleanup_retention

log = get_logger("netprobe.scheduler")

WAN_JOB_PREFIX = "wan_target_"


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    # --- Speedtest (blockierend -> Threadpool) ---
    async def _scheduled_speedtest(self) -> None:
        def _job() -> None:
            with session_scope() as db:
                if not kvstore.acquire_lock(db, speedtest_service.SPEEDTEST_LOCK, ttl_s=600):
                    log.info("Speedtest übersprungen (Lock aktiv)")
                    return
            try:
                with session_scope() as db:
                    speedtest_service.run_speedtest(db, source="scheduled")
            finally:
                with session_scope() as db:
                    kvstore.release_lock(db, speedtest_service.SPEEDTEST_LOCK)

        await asyncio.to_thread(_job)

    async def _scheduled_wan(self, target_id: int) -> None:
        def _job() -> None:
            with session_scope() as db:
                target = db.get(WanTarget, target_id)
                if target is None or not target.enabled:
                    return
                wan_service.run_target(db, target)

        await asyncio.to_thread(_job)

    async def _aggregate(self) -> None:
        await asyncio.to_thread(aggregate_ping_samples)

    async def _cleanup(self) -> None:
        await asyncio.to_thread(cleanup_retention)

    # --- Verwaltung ---
    def reschedule_speedtest(self) -> None:
        job_id = "speedtest"
        existing = self.scheduler.get_job(job_id)
        if existing:
            existing.remove()
        if settings.speedtest_enabled and settings.speedtest_interval_min > 0:
            self.scheduler.add_job(
                self._scheduled_speedtest,
                IntervalTrigger(minutes=settings.speedtest_interval_min),
                id=job_id,
                max_instances=1,
                coalesce=True,
            )
            log.info("Speedtest alle %d Minuten geplant", settings.speedtest_interval_min)

    def sync_wan_jobs(self) -> None:
        """Synchronisiert Scheduler-Jobs mit den aktivierten WAN-Zielen."""
        with session_scope() as db:
            targets = db.query(WanTarget).all()
            wanted: dict[str, WanTarget] = {
                f"{WAN_JOB_PREFIX}{t.id}": t for t in targets if t.enabled
            }

        for job in self.scheduler.get_jobs():
            if job.id.startswith(WAN_JOB_PREFIX) and job.id not in wanted:
                job.remove()

        for job_id, target in wanted.items():
            existing = self.scheduler.get_job(job_id)
            if existing:
                existing.remove()
            self.scheduler.add_job(
                self._scheduled_wan,
                IntervalTrigger(seconds=max(10, target.interval_s)),
                id=job_id,
                args=[target.id],
                max_instances=1,
                coalesce=True,
            )

    def start(self) -> None:
        self.reschedule_speedtest()
        self.sync_wan_jobs()
        self.scheduler.add_job(
            self._aggregate, IntervalTrigger(minutes=5), id="ping_aggregate", coalesce=True
        )
        self.scheduler.add_job(
            self._cleanup, IntervalTrigger(hours=6), id="retention_cleanup", coalesce=True
        )
        self.scheduler.start()
        log.info("Scheduler gestartet")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)


scheduler_service = SchedulerService()
