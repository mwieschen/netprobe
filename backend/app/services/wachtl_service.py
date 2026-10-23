"""Wachtl-Clients: Konfiguration für Probes, Speichern der Buckets, Übersicht, Zeitreihe, Retention.

Gemeinsam genutzt von der Probe-API (``app.api.wachtl_probe``) und der Admin-API (``app.api.clients``).
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.wachtl import (
    WachtlCheck,
    WachtlIngestBatch,
    WachtlProbe,
    WachtlSample,
    WachtlSite,
)
from app.services import kvstore
from app.services.wachtl_ingest import BUCKET_S, Bucket, classify_check, merge_bucket, spki_pin_from_pem

log = get_logger("netprobe.wachtl")

KV_ENABLED = "wachtl_enabled"
CHECK_TYPES = ("ping",)
ROLES = ("target", "gateway", "reference")
# Standardwerte der Ping-Parameter wie in Wachtl (checktypes.py)
PING_PARAM_DEFAULTS = {"size_bytes": 32, "ttl": 128, "jitter_window_s": 60}
HOUR_S = 3600
# Zeitraum, aus dem der aktuelle Zustand eines Checks bewertet wird
STATUS_WINDOW_S = 300


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def is_enabled(db: Session) -> bool:
    return bool(kvstore.get_value(db, KV_ENABLED, settings.wachtl_enabled))


def probe_online(probe: WachtlProbe, now: datetime | None = None) -> bool:
    if probe.last_seen is None or probe.status != "active":
        return False
    limit = max(90, 3 * settings.wachtl_heartbeat_interval_s)
    return ((now or utcnow()) - probe.last_seen).total_seconds() <= limit


def tls_pin() -> tuple[str | None, str | None]:
    """SPKI-Pin des nginx-Zertifikats → (pin, Fehlertext)."""
    path = settings.wachtl_tls_cert
    try:
        return spki_pin_from_pem(path.read_text(encoding="ascii", errors="replace")), None
    except FileNotFoundError:
        return None, f"Zertifikat {path} nicht gefunden – HTTPS ist nicht eingerichtet (install.sh)."
    except (OSError, ValueError) as exc:
        return None, f"Zertifikat {path} nicht lesbar: {exc}"


# --- Konfiguration ----------------------------------------------------------
def checks_for_probe(db: Session, probe: WachtlProbe) -> list[WachtlCheck]:
    return list(db.execute(
        select(WachtlCheck)
        .where(WachtlCheck.enabled.is_(True),
               or_(WachtlCheck.site_id.is_(None), WachtlCheck.site_id == probe.site_id))
        .order_by(WachtlCheck.id)
    ).scalars())


def check_params(check: WachtlCheck) -> dict:
    try:
        stored = json.loads(check.params_json or "{}")
    except json.JSONDecodeError:
        stored = {}
    params = dict(PING_PARAM_DEFAULTS)
    params.update({k: v for k, v in stored.items() if k in PING_PARAM_DEFAULTS})
    return params


def config_payload(db: Session, probe: WachtlProbe) -> tuple[dict, str]:
    """Konfiguration im Format von Wachtl ``GET /api/probe/v1/config`` und ihr ETag."""
    checks = [
        {
            "id": c.id,
            "type": c.type,
            "target": c.target,
            "role": c.role,
            "interval_s": c.interval_s,
            "timeout_ms": min(c.timeout_ms, c.interval_s * 1000),
            "params": check_params(c),
        }
        for c in checks_for_probe(db, probe)
    ]
    payload = {
        "probe_id": probe.id,
        "checks": checks,
        # iperf3-Gegenstelle, RTP-Reflektor und Client-Hinweise folgen in Phase 2/3.
        "iperf_server": {"enabled": False, "port": settings.iperf3_server_port},
        "rtp_reflector": {"enabled": False, "port": 5004},
        "server_urls": settings.wachtl_server_url_list,
        "upload_interval_s": settings.wachtl_upload_interval_s,
        "heartbeat_interval_s": settings.wachtl_heartbeat_interval_s,
        "config_poll_s": settings.wachtl_config_poll_s,
        "client_findings": {"enabled": False, "notify": False},
    }
    etag = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    payload["etag"] = etag
    return payload, etag


# --- Ingest -----------------------------------------------------------------
def batch_seen(db: Session, full_id: str) -> bool:
    return db.get(WachtlIngestBatch, full_id) is not None


def store_buckets(db: Session, probe_id: int, buckets: dict[tuple[int, int], Bucket]) -> None:
    """1-Minuten-Buckets einrechnen (bestehende Zeilen ergänzen, fehlende anlegen)."""
    if not buckets:
        return
    keys = {(cid, datetime.fromtimestamp(ts, UTC)): b for (cid, ts), b in buckets.items()}
    stamps = [k[1] for k in keys]
    existing = {
        (r.check_id, r.bucket_ts): r
        for r in db.execute(
            select(WachtlSample).where(
                WachtlSample.probe_id == probe_id,
                WachtlSample.step_s == BUCKET_S,
                WachtlSample.check_id.in_({k[0] for k in keys}),
                WachtlSample.bucket_ts >= min(stamps),
                WachtlSample.bucket_ts <= max(stamps),
            )
        ).scalars()
    }
    for (cid, bts), b in keys.items():
        row = existing.get((cid, bts))
        if row is None:
            row = WachtlSample(probe_id=probe_id, check_id=cid, bucket_ts=bts, step_s=BUCKET_S,
                               count=0, loss=0, rtt_sum=0.0, rtt_n=0, jitter_sum=0.0, jitter_n=0)
            db.add(row)
        merge_bucket(row, b)


# --- Übersicht und Zeitreihe -----------------------------------------------
def latest_stats(db: Session, since: datetime) -> dict[tuple[int, int], dict]:
    """Kennzahlen je (probe_id, check_id) seit ``since``."""
    rows = db.execute(
        select(
            WachtlSample.probe_id, WachtlSample.check_id,
            func.sum(WachtlSample.count), func.sum(WachtlSample.loss),
            func.sum(WachtlSample.rtt_sum), func.sum(WachtlSample.rtt_n),
            func.sum(WachtlSample.jitter_sum), func.sum(WachtlSample.jitter_n),
            func.max(WachtlSample.bucket_ts),
        )
        .where(WachtlSample.bucket_ts >= since)
        .group_by(WachtlSample.probe_id, WachtlSample.check_id)
    ).all()
    out: dict[tuple[int, int], dict] = {}
    for pid, cid, cnt, loss, rsum, rn, jsum, jn, last in rows:
        cnt, loss = int(cnt or 0), int(loss or 0)
        rtt_avg = round(rsum / rn, 2) if rn else None
        out[(pid, cid)] = {
            "count": cnt,
            "loss": loss,
            "loss_pct": round(loss * 100.0 / cnt, 2) if cnt else None,
            "rtt_avg": rtt_avg,
            "jitter_ms": round(jsum / jn, 2) if jn else None,
            "last_ts": last,
            "state": classify_check(cnt, loss, rtt_avg,
                                    loss_warn_pct=settings.report_ping_loss_warn_pct,
                                    latency_warn_ms=settings.diagnostics_ping_latency_warn_ms),
        }
    return out


def overview(db: Session) -> dict:
    now = utcnow()
    stats = latest_stats(db, now - timedelta(seconds=STATUS_WINDOW_S))
    sites = list(db.execute(select(WachtlSite).order_by(WachtlSite.name)).scalars())
    probes = list(db.execute(select(WachtlProbe).order_by(WachtlProbe.hostname)).scalars())
    checks = list(db.execute(select(WachtlCheck).order_by(WachtlCheck.id)).scalars())
    out_sites = []
    for s in sites:
        site_checks = [c for c in checks if c.enabled and c.site_id in (None, s.id)]
        out_probes = []
        for p in (p for p in probes if p.site_id == s.id):
            online = probe_online(p, now)
            cells = []
            for c in site_checks:
                st = stats.get((p.id, c.id))
                state = st["state"] if st else "stale"
                if not online and p.status == "active":
                    state = "stale"
                cells.append({"check_id": c.id, "state": state, **({k: st[k] for k in (
                    "loss_pct", "rtt_avg", "jitter_ms", "count")} if st else {
                    "loss_pct": None, "rtt_avg": None, "jitter_ms": None, "count": 0})})
            out_probes.append({"probe_id": p.id, "hostname": p.hostname, "online": online,
                               "status": p.status, "last_seen": p.last_seen, "cells": cells})
        out_sites.append({
            "site_id": s.id, "name": s.name,
            "checks": [{"id": c.id, "name": c.name, "target": c.target, "role": c.role} for c in site_checks],
            "probes": out_probes,
        })
    return {"sites": out_sites, "window_s": STATUS_WINDOW_S}


def _epoch_bucket(col, bucket_s: int):
    return (cast(func.strftime("%s", col), Integer) // bucket_s) * bucket_s


def series(db: Session, probe_id: int, check_id: int, since: datetime | None, until: datetime | None,
           points: int = 1500) -> list[dict]:
    """``[{ts, count, loss, rtt_min, rtt_avg, rtt_max, jitter_ms}]`` – Format wie ``PingSeriesPoint``."""
    conds = [WachtlSample.probe_id == probe_id, WachtlSample.check_id == check_id]
    if since is None:
        since = db.execute(select(func.min(WachtlSample.bucket_ts)).where(*conds)).scalar()
        if since is None:
            return []
    hi = until or utcnow()
    if since >= hi:
        return []
    bucket_s = max(BUCKET_S, math.ceil((hi - since).total_seconds() / max(points, 1)))
    b = _epoch_bucket(WachtlSample.bucket_ts, bucket_s).label("b")
    rows = db.execute(
        select(
            b, func.sum(WachtlSample.count), func.sum(WachtlSample.loss),
            func.min(WachtlSample.rtt_min), func.sum(WachtlSample.rtt_sum), func.sum(WachtlSample.rtt_n),
            func.max(WachtlSample.rtt_max),
            func.sum(WachtlSample.jitter_sum), func.sum(WachtlSample.jitter_n),
        )
        .where(*conds, WachtlSample.bucket_ts >= since, WachtlSample.bucket_ts <= hi)
        .group_by(b)
        .order_by(b)
    ).all()
    return [
        {
            "ts": datetime.fromtimestamp(int(bts), UTC).isoformat(),
            "count": int(cnt or 0),
            "loss": int(loss or 0),
            "rtt_min": rmin,
            "rtt_avg": round(rsum / rn, 3) if rn else None,
            "rtt_max": rmax,
            "jitter_ms": round(jsum / jn, 3) if jn else None,
        }
        for bts, cnt, loss, rmin, rsum, rn, rmax, jsum, jn in rows
    ]


# --- Verdichtung und Retention ---------------------------------------------
def rollup_and_prune() -> None:
    """1-min-Buckets älter als ``wachtl_minute_retention_days`` zu 1-h-Buckets zusammenfassen,
    alte Stunden-Buckets und Batch-IDs löschen. Läuft im Housekeeping-Job (``cleanup_retention``)."""
    now = utcnow()
    cutoff_epoch = int((now - timedelta(days=settings.wachtl_minute_retention_days)).timestamp())
    cutoff = datetime.fromtimestamp(cutoff_epoch - cutoff_epoch % HOUR_S, UTC)
    with session_scope() as db:
        hb = _epoch_bucket(WachtlSample.bucket_ts, HOUR_S).label("hb")
        old = WachtlSample.step_s == BUCKET_S, WachtlSample.bucket_ts < cutoff
        groups = db.execute(
            select(
                WachtlSample.probe_id, WachtlSample.check_id, hb,
                func.sum(WachtlSample.count), func.sum(WachtlSample.loss),
                func.min(WachtlSample.rtt_min), func.max(WachtlSample.rtt_max),
                func.sum(WachtlSample.rtt_sum), func.sum(WachtlSample.rtt_n),
                func.sum(WachtlSample.jitter_sum), func.sum(WachtlSample.jitter_n),
                func.max(WachtlSample.ttl),
            ).where(*old).group_by(WachtlSample.probe_id, WachtlSample.check_id, hb)
        ).all()
        for pid, cid, hts, cnt, loss, rmin, rmax, rsum, rn, jsum, jn, ttl in groups:
            bts = datetime.fromtimestamp(int(hts), UTC)
            row = db.execute(select(WachtlSample).where(
                WachtlSample.probe_id == pid, WachtlSample.check_id == cid,
                WachtlSample.step_s == HOUR_S, WachtlSample.bucket_ts == bts)).scalar_one_or_none()
            if row is None:
                row = WachtlSample(probe_id=pid, check_id=cid, bucket_ts=bts, step_s=HOUR_S,
                                   count=0, loss=0, rtt_sum=0.0, rtt_n=0, jitter_sum=0.0, jitter_n=0)
                db.add(row)
            merge_bucket(row, Bucket(count=int(cnt or 0), loss=int(loss or 0), rtt_min=rmin, rtt_max=rmax,
                                     rtt_sum=float(rsum or 0), rtt_n=int(rn or 0),
                                     jitter_sum=float(jsum or 0), jitter_n=int(jn or 0), ttl=ttl))
        db.flush()
        n_min = db.query(WachtlSample).filter(*old).delete(synchronize_session=False)
        n_hour = db.query(WachtlSample).filter(
            WachtlSample.bucket_ts < now - timedelta(days=settings.wachtl_retention_days)
        ).delete(synchronize_session=False)
        n_batch = db.query(WachtlIngestBatch).filter(
            WachtlIngestBatch.received_at < now - timedelta(days=9)
        ).delete(synchronize_session=False)
        if groups or n_hour or n_batch:
            log.info("Wachtl: %d Stunden-Buckets verdichtet (%d Minuten-Buckets), %d alte Buckets, "
                     "%d Batch-IDs gelöscht", len(groups), n_min, n_hour, n_batch)
