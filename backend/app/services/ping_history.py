"""Dauerping-Verlauf: Zeitreihe und Kennzahlen für beliebige Zeiträume.

Rohdaten (``ping_samples``) existieren nur ``ping_raw_retention_days`` lang, danach
nur noch 5-Minuten-Aggregate (``ping_aggregates``). Beide Quellen werden hier so
kombiniert, dass das Diagramm für jeden Zeitraum – von 5 Minuten bis 365 Tage –
eine begrenzte Anzahl Punkte über den **gesamten** Zeitraum liefert. Gebündelt wird
per SQL (``GROUP BY``), damit auch Wochen mit Sekundenintervall nicht komplett in
den Speicher geladen werden.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.timeutils import utcnow
from app.models.ping import PingAggregate, PingSample

# Rohwerte werden ungebündelt geliefert, solange es höchstens ~3,3 × ``points`` sind
# (Standard 1500 → 5000, z. B. 1 h bei 1-s-Intervall), absolut höchstens MAX_RAW_POINTS.
# Die Oberfläche lädt das sichtbare Fenster plus Puffer zum Verschieben (3-fache Breite)
# mit ``points=4500`` – so bleibt ein 1-h-Ausschnitt auch dann sekundengenau.
MAX_RAW_POINTS = 15000
AGGREGATE_BUCKET_S = 300


def _oldest_raw(db: Session, job_id: int) -> datetime | None:
    return db.execute(select(func.min(PingSample.ts)).where(PingSample.job_id == job_id)).scalar()


def _oldest_agg(db: Session, job_id: int) -> datetime | None:
    return db.execute(
        select(func.min(PingAggregate.bucket_ts)).where(PingAggregate.job_id == job_id)
    ).scalar()


def _epoch_bucket(col, bucket_s: int):
    # SQLite: Sekunden seit Epoch, ganzzahlig auf den Bucket abgerundet.
    return (cast(func.strftime("%s", col), Integer) // bucket_s) * bucket_s


def _iso(sec: int) -> str:
    return datetime.fromtimestamp(int(sec), UTC).isoformat()


def _ok_rtt():
    return case((PingSample.ok.is_(True), PingSample.rtt_ms), else_=None)


def _agg_ok_count():
    return PingAggregate.count - PingAggregate.loss


def _range_conds(col, since: datetime | None, until: datetime | None) -> list:
    conds = []
    if since is not None:
        conds.append(col >= since)
    if until is not None:
        conds.append(col <= until)
    return conds


def series(
    db: Session, job_id: int, since: datetime | None, until: datetime | None, points: int = 1500
) -> list[dict]:
    """Liefert ``[{ts, count, loss, rtt_min, rtt_avg, rtt_max}]`` aufsteigend nach Zeit."""
    oldest_raw = _oldest_raw(db, job_id)
    oldest_agg = _oldest_agg(db, job_id)
    known = [t for t in (oldest_raw, oldest_agg) if t is not None]
    if not known:
        return []
    lo = since or min(known)
    hi = until or utcnow()
    if lo >= hi:
        return []
    bucket_s = max(1, math.ceil((hi - lo).total_seconds() / max(points, 1)))
    out: list[dict] = []

    # Teil 1: Aggregate für den Bereich vor dem ältesten noch vorhandenen Rohwert.
    agg_hi = oldest_raw if oldest_raw is not None else hi
    if oldest_agg is not None and lo < agg_hi:
        b = _epoch_bucket(PingAggregate.bucket_ts, max(bucket_s, AGGREGATE_BUCKET_S)).label("b")
        ok_cnt = _agg_ok_count()
        rows = db.execute(
            select(
                b,
                func.sum(PingAggregate.count),
                func.sum(PingAggregate.loss),
                func.min(PingAggregate.rtt_min),
                func.sum(PingAggregate.rtt_avg * ok_cnt),
                func.sum(case((PingAggregate.rtt_avg.is_not(None), ok_cnt), else_=0)),
                func.max(PingAggregate.rtt_max),
            )
            .where(
                PingAggregate.job_id == job_id,
                PingAggregate.bucket_ts >= lo,
                PingAggregate.bucket_ts < agg_hi,
                PingAggregate.bucket_ts <= hi,
            )
            .group_by(b)
            .order_by(b)
        ).all()
        for bts, cnt, loss, rmin, wsum, wcnt, rmax in rows:
            out.append({
                "ts": _iso(bts), "count": int(cnt or 0), "loss": int(loss or 0),
                "rtt_min": rmin, "rtt_avg": round(wsum / wcnt, 3) if wcnt else None, "rtt_max": rmax,
            })

    # Teil 2: Rohdaten – bei wenigen Punkten ungebündelt, sonst per SQL gebündelt.
    if oldest_raw is not None and oldest_raw <= hi:
        conds = [PingSample.job_id == job_id, PingSample.ts >= max(lo, oldest_raw), PingSample.ts <= hi]
        n = db.execute(select(func.count()).select_from(PingSample).where(*conds)).scalar() or 0
        if n <= min(MAX_RAW_POINTS, points * 10 // 3):
            for s in db.execute(select(PingSample).where(*conds).order_by(PingSample.ts)).scalars():
                rtt = s.rtt_ms if s.ok else None
                out.append({
                    "ts": s.ts.isoformat(), "count": 1, "loss": 0 if s.ok else 1,
                    "rtt_min": rtt, "rtt_avg": rtt, "rtt_max": rtt,
                })
        else:
            b = _epoch_bucket(PingSample.ts, bucket_s).label("b")
            ok_rtt = _ok_rtt()
            rows = db.execute(
                select(
                    b,
                    func.count(),
                    func.sum(case((PingSample.ok.is_(True), 0), else_=1)),
                    func.min(ok_rtt),
                    func.avg(ok_rtt),
                    func.max(ok_rtt),
                )
                .where(*conds)
                .group_by(b)
                .order_by(b)
            ).all()
            for bts, cnt, loss, rmin, ravg, rmax in rows:
                out.append({
                    "ts": _iso(bts), "count": int(cnt), "loss": int(loss or 0),
                    "rtt_min": rmin, "rtt_avg": round(ravg, 3) if ravg is not None else None,
                    "rtt_max": rmax,
                })
    return out


def stats(db: Session, job_id: int, since: datetime | None, until: datetime | None) -> dict:
    """Kennzahlen (Anzahl, Verlust, RTT min/ø/max) über Roh- und Aggregatdaten."""
    oldest_raw = _oldest_raw(db, job_id)
    count = loss = 0
    wsum = 0.0
    wcnt = 0
    mins: list[float] = []
    maxs: list[float] = []

    def _add(c, lo_, mn, sm, n_ok, mx) -> None:
        nonlocal count, loss, wsum, wcnt
        count += int(c or 0)
        loss += int(lo_ or 0)
        wsum += float(sm or 0.0)
        wcnt += int(n_ok or 0)
        if mn is not None:
            mins.append(mn)
        if mx is not None:
            maxs.append(mx)

    if oldest_raw is not None:
        ok_rtt = _ok_rtt()
        _add(*db.execute(
            select(
                func.count(),
                func.sum(case((PingSample.ok.is_(True), 0), else_=1)),
                func.min(ok_rtt),
                func.sum(ok_rtt),
                func.count(ok_rtt),
                func.max(ok_rtt),
            ).where(PingSample.job_id == job_id, *_range_conds(PingSample.ts, since, until))
        ).one())

    if since is None or oldest_raw is None or since < oldest_raw:
        ok_cnt = _agg_ok_count()
        conds = [PingAggregate.job_id == job_id, *_range_conds(PingAggregate.bucket_ts, since, until)]
        if oldest_raw is not None:
            conds.append(PingAggregate.bucket_ts < oldest_raw)
        _add(*db.execute(
            select(
                func.sum(PingAggregate.count),
                func.sum(PingAggregate.loss),
                func.min(PingAggregate.rtt_min),
                func.sum(PingAggregate.rtt_avg * ok_cnt),
                func.sum(case((PingAggregate.rtt_avg.is_not(None), ok_cnt), else_=0)),
                func.max(PingAggregate.rtt_max),
            ).where(*conds)
        ).one())

    return {
        "job_id": job_id,
        "count": count,
        "loss": loss,
        "loss_pct": round(loss / count * 100, 2) if count else 0.0,
        "rtt_min": round(min(mins), 3) if mins else None,
        "rtt_avg": round(wsum / wcnt, 3) if wcnt else None,
        "rtt_max": round(max(maxs), 3) if maxs else None,
    }
