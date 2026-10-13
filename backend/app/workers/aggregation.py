"""Aggregation der Dauerping-Rohdaten in 5-Minuten-Buckets und Retention."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.health_check import HealthCheckResult
from app.models.ping import PingAggregate, PingSample
from app.models.retransmission import RetransmissionTest
from app.models.speedtest import SpeedtestResult
from app.models.traceroute import GeoipCache, Traceroute
from app.models.voip import VoipTest
from app.models.wan import WanResult

log = get_logger("netprobe.aggregation")

BUCKET_SECONDS = 300


def _floor_bucket(dt):
    epoch = int(dt.timestamp())
    floored = epoch - (epoch % BUCKET_SECONDS)
    from datetime import UTC, datetime

    return datetime.fromtimestamp(floored, tz=UTC)


def aggregate_ping_samples() -> None:
    """Bildet 5-Minuten-Aggregate für abgeschlossene Buckets."""
    now = utcnow()
    current_bucket = _floor_bucket(now)

    with session_scope() as db:
        job_ids = [row[0] for row in db.execute(select(PingSample.job_id).distinct())]
        for job_id in job_ids:
            # letzten aggregierten Bucket ermitteln
            last = db.execute(
                select(func.max(PingAggregate.bucket_ts)).where(PingAggregate.job_id == job_id)
            ).scalar()

            q = select(PingSample).where(PingSample.job_id == job_id)
            if last is not None:
                q = q.where(PingSample.ts >= last + timedelta(seconds=BUCKET_SECONDS))
            samples = db.execute(q.order_by(PingSample.ts)).scalars().all()

            buckets: dict = {}
            for s in samples:
                b = _floor_bucket(s.ts)
                if b >= current_bucket:
                    continue  # laufenden Bucket auslassen
                buckets.setdefault(b, []).append(s)

            for bucket_ts, group in buckets.items():
                rtts = [s.rtt_ms for s in group if s.ok and s.rtt_ms is not None]
                loss = sum(1 for s in group if not s.ok)
                agg = PingAggregate(
                    job_id=job_id,
                    bucket_ts=bucket_ts,
                    count=len(group),
                    loss=loss,
                    rtt_min=min(rtts) if rtts else None,
                    rtt_avg=round(sum(rtts) / len(rtts), 3) if rtts else None,
                    rtt_max=max(rtts) if rtts else None,
                )
                db.add(agg)
            if buckets:
                log.info("Job %s: %d Buckets aggregiert", job_id, len(buckets))


def _unlink_quiet(path_str: str | None) -> None:
    if not path_str:
        return
    try:
        from pathlib import Path

        Path(path_str).unlink(missing_ok=True)
    except OSError:
        pass


def _prune_voip_captures(cutoff) -> None:
    """Entfernt verwaiste VoIP-Mitschnitte, die älter als der Cutoff sind."""
    from pathlib import Path

    voip_dir = Path(settings.capture_dir) / "voip"
    if not voip_dir.is_dir():
        return
    cutoff_ts = cutoff.timestamp()
    for f in voip_dir.glob("voiptest_*.pcapng"):
        try:
            if f.stat().st_mtime < cutoff_ts:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _prune_retransmission_captures(cutoff) -> None:
    """Entfernt liegen gebliebene Retransmission-Mitschnitte (normalerweise
    löscht ``run_test`` die PCAP sofort nach der Analyse; nur Reste nach einem
    Absturz bleiben hier übrig)."""
    from pathlib import Path

    retrans_dir = Path(settings.capture_dir) / "retransmission"
    if not retrans_dir.is_dir():
        return
    cutoff_ts = cutoff.timestamp()
    for f in retrans_dir.glob("retrans_*.pcapng"):
        try:
            if f.stat().st_mtime < cutoff_ts:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_retention() -> None:
    """Löscht abgelaufene Rohdaten gemäß Retention-Konfiguration."""
    now = utcnow()
    with session_scope() as db:
        raw_cutoff = now - timedelta(days=settings.ping_raw_retention_days)
        n1 = db.query(PingSample).filter(PingSample.ts < raw_cutoff).delete()

        agg_cutoff = now - timedelta(days=settings.ping_agg_retention_days)
        n2 = db.query(PingAggregate).filter(PingAggregate.bucket_ts < agg_cutoff).delete()

        res_cutoff = now - timedelta(days=settings.result_retention_days)
        n3 = db.query(SpeedtestResult).filter(SpeedtestResult.ts < res_cutoff).delete()
        n4 = db.query(WanResult).filter(WanResult.ts < res_cutoff).delete()

        voip_cutoff = now - timedelta(days=settings.voip_retention_days)
        # Mitschnitt-Dateien der abgelaufenen VoIP-Tests vor dem Löschen entfernen.
        for (cap_path,) in db.query(VoipTest.capture_path).filter(
            VoipTest.ts < voip_cutoff, VoipTest.capture_path.is_not(None)
        ):
            _unlink_quiet(cap_path)
        n5 = db.query(VoipTest).filter(VoipTest.ts < voip_cutoff).delete()
        _prune_voip_captures(voip_cutoff)

        tr_cutoff = now - timedelta(days=settings.traceroute_retention_days)
        n6 = db.query(Traceroute).filter(Traceroute.ts < tr_cutoff).delete()

        geo_cutoff = now - timedelta(days=max(180, settings.geoip_cache_days * 4))
        n7 = db.query(GeoipCache).filter(GeoipCache.fetched_at < geo_cutoff).delete()

        hc_cutoff = now - timedelta(days=settings.health_check_retention_days)
        n8 = db.query(HealthCheckResult).filter(HealthCheckResult.ts < hc_cutoff).delete()

        retrans_cutoff = now - timedelta(days=settings.retrans_retention_days)
        # Targets/Timeline hängen per ON DELETE CASCADE an retransmission_tests.
        n9 = db.query(RetransmissionTest).filter(RetransmissionTest.ts < retrans_cutoff).delete()
        _prune_retransmission_captures(retrans_cutoff)

        if n1 or n2 or n3 or n4 or n5 or n6 or n7 or n8 or n9:
            log.info(
                "Retention: %d Samples, %d Aggregate, %d Speedtests, %d WAN-Ergebnisse, "
                "%d VoIP-Tests, %d Traceroutes, %d Geo-Cache-Einträge, %d Health-Ergebnisse, "
                "%d Retransmission-Tests gelöscht",
                n1, n2, n3, n4, n5, n6, n7, n8, n9,
            )
