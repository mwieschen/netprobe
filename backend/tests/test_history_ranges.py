"""Zeitraum-Auswahl der Verlaufsdiagramme: Presets, absolute Zeiträume, Ausdünnung
und Rückgriff auf Ping-Aggregate für ältere Aufzeichnungen."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utcnow


def _make_ping_job() -> tuple[int, object]:
    from app.db.session import session_scope
    from app.models.ping import PingAggregate, PingJob, PingSample

    now = utcnow().replace(microsecond=0)
    with session_scope() as db:
        job = PingJob(name="hist", host="192.0.2.1", interval_s=1, status="stopped")
        db.add(job)
        db.flush()
        # Rohdaten: letzte 2 Stunden, alle 1 s (7200 Stück, > MAX_RAW_POINTS).
        db.add_all(
            PingSample(job_id=job.id, ts=now - timedelta(seconds=i), ok=i % 100 != 0,
                       rtt_ms=None if i % 100 == 0 else 10.0 + (i % 7))
            for i in range(7200)
        )
        # Ältere Aufzeichnungen nur noch als 5-Minuten-Aggregate (vor 20–30 Tagen).
        for k in range(0, 10 * 24 * 12, 6):
            db.add(PingAggregate(
                job_id=job.id, bucket_ts=now - timedelta(days=20, minutes=5 * k),
                count=300, loss=3, rtt_min=8.0, rtt_avg=20.0, rtt_max=50.0,
            ))
        return job.id, now


def test_resolve_range_presets_and_absolute():
    import pytest
    from fastapi import HTTPException

    from app.api.timerange import resolve_range

    since, until = resolve_range("7d", None, None)
    assert until is None and since is not None
    assert abs((utcnow() - since) - timedelta(days=7)) < timedelta(seconds=5)
    assert resolve_range("all") == (None, None)
    s, e = resolve_range("24h", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00+01:00")
    assert s is not None and e is not None
    assert s.isoformat() == "2026-01-01T00:00:00+00:00"
    assert e.isoformat() == "2026-01-01T23:00:00+00:00"
    with pytest.raises(HTTPException):
        resolve_range(None, "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")
    with pytest.raises(HTTPException):
        resolve_range(None, "kein-datum", None)


def test_ping_series_covers_full_range(admin):
    job_id, now = _make_ping_job()

    # 1 h → unter MAX_RAW_POINTS → Rohwerte.
    pts = admin.get(f"/api/ping/jobs/{job_id}/series?range=1h").json()
    assert 3500 <= len(pts) <= 3700
    assert all(p["count"] == 1 for p in pts)

    # 6 h → mehr Rohwerte als erlaubt → gebündelt, aber über die vollen 2 h Daten.
    pts = admin.get(f"/api/ping/jobs/{job_id}/series?range=6h&points=500").json()
    assert 0 < len(pts) <= 500
    assert sum(p["count"] for p in pts) == 7200
    assert sum(p["loss"] for p in pts) == 72

    # Alter Zeitraum (vor 20–30 Tagen) → nur Aggregate, absolute Start/Ende-Angabe.
    start = (now - timedelta(days=31)).isoformat()
    end = (now - timedelta(days=19)).isoformat()
    resp = admin.get(f"/api/ping/jobs/{job_id}/series", params={"start": start, "end": end})
    assert resp.status_code == 200, resp.text
    pts = resp.json()
    assert pts and all(p["rtt_avg"] == 20.0 for p in pts)

    stats = admin.get(f"/api/ping/jobs/{job_id}/stats", params={"start": start, "end": end}).json()
    assert stats["count"] == 480 * 300
    assert stats["loss_pct"] == 1.0
    assert stats["rtt_min"] == 8.0 and stats["rtt_max"] == 50.0

    # Gesamter Zeitraum kombiniert Aggregate + Rohdaten.
    stats = admin.get(f"/api/ping/jobs/{job_id}/stats?range=all").json()
    assert stats["count"] == 480 * 300 + 7200


def test_wan_results_thinned_over_whole_range(admin):
    from app.db.session import session_scope
    from app.models.wan import WanResult, WanTarget

    now = utcnow()
    with session_scope() as db:
        t = WanTarget(name="thin", host="192.0.2.2", interval_s=60, count=5, timeout_ms=1000, enabled=False)
        db.add(t)
        db.flush()
        db.add_all(
            WanResult(target_id=t.id, ts=now - timedelta(minutes=i), ok=True, rtt_avg=1.0, loss_pct=0.0)
            for i in range(7 * 24 * 60)
        )
        tid = t.id

    rows = admin.get(f"/api/wan/targets/{tid}/results?range=7d&limit=1000").json()
    assert len(rows) <= 1001
    oldest = min(r["ts"] for r in rows)
    newest = max(r["ts"] for r in rows)
    # Früher: nur die neuesten 2000 Einträge (~1,4 Tage). Jetzt: ganzer Zeitraum.
    from datetime import datetime

    span = datetime.fromisoformat(newest) - datetime.fromisoformat(oldest)
    assert span > timedelta(days=6, hours=20)
    assert rows[0]["ts"] >= rows[-1]["ts"]  # neueste zuerst (API-kompatibel)

    start = (now - timedelta(days=3)).isoformat()
    end = (now - timedelta(days=2)).isoformat()
    rows = admin.get(f"/api/wan/targets/{tid}/results", params={"start": start, "end": end}).json()
    assert 1400 <= len(rows) <= 1441

    resp = admin.get(f"/api/wan/targets/{tid}/results", params={"start": "x"})
    assert resp.status_code == 422
