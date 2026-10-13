"""APScheduler-basierter Hintergrund-Scheduler (läuft im API-Prozess, eine einzige
``AsyncIOScheduler``-Instanz für alle automatisierten Tests).

Aufgaben:
- Geplante Speedtests (konfigurierbares Intervall, mit Job-Lock).
- Geplante WAN-/VoIP-/Traceroute-/Health-Messungen je aktiviertem Ziel.
- Geplantes TCP-Retransmission-Monitoring (ein Interface, konfigurierbares Intervall).
- 5-Minuten-Aggregation der Dauerpings, Retention-Cleanup (Housekeeping, läuft immer).

``self.enabled`` ist ein globaler Automatisierungs-Schalter (``/api/scheduler/enable|disable``,
im KV-Store gespiegelt): pausiert/resumed alle Test-Jobs ohne sie zu entfernen. Housekeeping
(``_HOUSEKEEPING_JOB_IDS``) läuft davon unabhängig weiter.
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.health_check import HealthProbe
from app.models.traceroute import TracerouteTarget
from app.models.voip import VoipServer
from app.models.wan import WanTarget
from app.services import (
    capture_service,
    kvstore,
    retransmission_service,
    speedtest_service,
    traceroute_service,
    voip_service,
    wan_service,
)
from app.services import health as health_framework
from app.workers.aggregation import aggregate_ping_samples, cleanup_retention

log = get_logger("netprobe.scheduler")

WAN_JOB_PREFIX = "wan_target_"
VOIP_JOB_PREFIX = "voip_server_"
TRACEROUTE_JOB_PREFIX = "traceroute_target_"
HEALTH_JOB_PREFIX = "health_probe_"
RETRANS_JOB_ID = "retransmission_monitor"
# Housekeeping-Jobs laufen unabhängig vom globalen Automatisierungs-Schalter weiter
# (keine "Tests", sondern interne Wartung).
_HOUSEKEEPING_JOB_IDS = frozenset({"ping_aggregate", "retention_cleanup"})


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        # Globaler Automatisierungs-Schalter (siehe /api/scheduler/enable|disable). Pausiert/
        # resumed alle Test-Jobs, ohne sie zu entfernen; Housekeeping bleibt unberührt.
        self.enabled: bool = settings.scheduler_enabled

    def _apply_enabled_state(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id in _HOUSEKEEPING_JOB_IDS:
                continue
            if self.enabled:
                job.resume()
            else:
                job.pause()

    def set_enabled(self, enabled: bool) -> None:
        """Schaltet die globale Automatisierung um (wirkt sofort auf laufende Jobs)."""
        self.enabled = enabled
        self._apply_enabled_state()
        log.info("Automatisierung %s", "aktiviert" if enabled else "pausiert")

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

    async def _scheduled_voip(self, server_id: int) -> None:
        def _job() -> None:
            with session_scope() as db:
                server = db.get(VoipServer, server_id)
                if server is None or not server.enabled:
                    return
                voip_service.run_test(db, server, source="scheduled")

        await asyncio.to_thread(_job)

    async def _scheduled_traceroute(self, target_id: int) -> None:
        def _job() -> None:
            with session_scope() as db:
                target = db.get(TracerouteTarget, target_id)
                if target is None or not target.enabled:
                    return
                traceroute_service.run(
                    db, host=target.host, mode=target.mode, tcp_port=target.tcp_port,
                    queries=target.queries, max_hops=target.max_hops,
                    resolve_geo=target.resolve_geo, source="scheduled", target_id=target.id,
                )

        await asyncio.to_thread(_job)

    async def _scheduled_health(self, probe_id: int) -> None:
        def _job() -> None:
            with session_scope() as db:
                probe = db.get(HealthProbe, probe_id)
                if probe is None or not probe.enabled:
                    return
                health_framework.run_probe(db, probe, source="scheduled")

        await asyncio.to_thread(_job)

    async def _scheduled_retransmission(self) -> None:
        def _job() -> None:
            with session_scope() as db:
                retransmission_service.run_test(
                    db,
                    interface=capture_service.resolve_default_interface(),
                    duration_s=settings.retrans_default_duration_s,
                    source="scheduled",
                )

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
        self._apply_enabled_state()

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
        self._apply_enabled_state()

    def sync_voip_jobs(self) -> None:
        """Synchronisiert Scheduler-Jobs mit den aktivierten 3CX-Servern."""
        with session_scope() as db:
            servers = db.query(VoipServer).all()
            wanted: dict[str, VoipServer] = {
                f"{VOIP_JOB_PREFIX}{s.id}": s for s in servers if s.enabled
            }

        for job in self.scheduler.get_jobs():
            if job.id.startswith(VOIP_JOB_PREFIX) and job.id not in wanted:
                job.remove()

        for job_id, server in wanted.items():
            existing = self.scheduler.get_job(job_id)
            if existing:
                existing.remove()
            self.scheduler.add_job(
                self._scheduled_voip,
                IntervalTrigger(seconds=max(60, server.interval_s)),
                id=job_id,
                args=[server.id],
                max_instances=1,
                coalesce=True,
            )
        self._apply_enabled_state()

    def sync_traceroute_jobs(self) -> None:
        """Synchronisiert Scheduler-Jobs mit den aktivierten Traceroute-Zielen."""
        with session_scope() as db:
            targets = db.query(TracerouteTarget).all()
            wanted: dict[str, TracerouteTarget] = {
                f"{TRACEROUTE_JOB_PREFIX}{t.id}": t for t in targets if t.enabled
            }

        for job in self.scheduler.get_jobs():
            if job.id.startswith(TRACEROUTE_JOB_PREFIX) and job.id not in wanted:
                job.remove()

        for job_id, target in wanted.items():
            existing = self.scheduler.get_job(job_id)
            if existing:
                existing.remove()
            self.scheduler.add_job(
                self._scheduled_traceroute,
                IntervalTrigger(seconds=max(60, target.interval_s)),
                id=job_id,
                args=[target.id],
                max_instances=1,
                coalesce=True,
            )
        self._apply_enabled_state()

    def sync_health_jobs(self) -> None:
        """Synchronisiert Scheduler-Jobs mit den aktivierten Health-Probes."""
        with session_scope() as db:
            probes = db.query(HealthProbe).all()
            wanted: dict[str, HealthProbe] = {
                f"{HEALTH_JOB_PREFIX}{p.id}": p for p in probes if p.enabled
            }

        for job in self.scheduler.get_jobs():
            if job.id.startswith(HEALTH_JOB_PREFIX) and job.id not in wanted:
                job.remove()

        for job_id, probe in wanted.items():
            existing = self.scheduler.get_job(job_id)
            if existing:
                existing.remove()
            self.scheduler.add_job(
                self._scheduled_health,
                IntervalTrigger(seconds=max(60, probe.interval_s)),
                id=job_id,
                args=[probe.id],
                max_instances=1,
                coalesce=True,
            )
        self._apply_enabled_state()

    def reschedule_retransmission(self) -> None:
        """Geplantes periodisches Retransmission-Monitoring (optional, 0 = aus)."""
        existing = self.scheduler.get_job(RETRANS_JOB_ID)
        if existing:
            existing.remove()
        if settings.retrans_enabled and settings.retrans_scheduled_interval_min > 0:
            self.scheduler.add_job(
                self._scheduled_retransmission,
                IntervalTrigger(minutes=max(5, settings.retrans_scheduled_interval_min)),
                id=RETRANS_JOB_ID,
                max_instances=1,
                coalesce=True,
            )
            log.info(
                "Retransmission-Monitoring alle %d Minuten geplant (%s)",
                settings.retrans_scheduled_interval_min, settings.retrans_default_interface,
            )
        self._apply_enabled_state()

    def start(self) -> None:
        with session_scope() as db:
            self.enabled = kvstore.get_value(db, "scheduler_enabled", settings.scheduler_enabled)
        self.reschedule_speedtest()
        self.sync_wan_jobs()
        self.sync_voip_jobs()
        self.sync_traceroute_jobs()
        self.sync_health_jobs()
        self.reschedule_retransmission()
        self.scheduler.add_job(
            self._aggregate, IntervalTrigger(minutes=5), id="ping_aggregate", coalesce=True
        )
        self.scheduler.add_job(
            self._cleanup, IntervalTrigger(hours=6), id="retention_cleanup", coalesce=True
        )
        self.scheduler.start()
        self._apply_enabled_state()
        log.info("Scheduler gestartet (Automatisierung %s)", "aktiv" if self.enabled else "pausiert")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)


scheduler_service = SchedulerService()
