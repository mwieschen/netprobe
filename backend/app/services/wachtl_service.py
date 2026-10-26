"""Wachtl-Clients: Konfiguration für Probes, Speichern der Buckets, Übersicht, Zeitreihe, Retention.

Gemeinsam genutzt von der Probe-API (``app.api.wachtl_probe``) und der Admin-API (``app.api.clients``).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.wachtl import (
    WachtlCheck,
    WachtlFinding,
    WachtlIngestBatch,
    WachtlProbe,
    WachtlReport,
    WachtlSample,
    WachtlSite,
)
from app.services import kvstore, wachtl_analysis, wachtl_measure
from app.services.wachtl_ingest import BUCKET_S, Bucket, classify_check, merge_bucket, spki_pin_from_pem

log = get_logger("netprobe.wachtl")

KV_ENABLED = "wachtl_enabled"
ROLES = ("target", "gateway", "reference")
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


def config_payload(db: Session, probe: WachtlProbe, host: str = "") -> tuple[dict, str]:
    """Konfiguration im Format von Wachtl ``GET /api/probe/v1/config`` und ihr ETag.

    ``host``: Adresse, unter der die Probe netprobe erreicht hat – Gegenstelle für iperf3/RTP, wenn
    ``wachtl_peer_host`` nicht gesetzt ist. Der Zustand der Gegenstellen geht ins ETag ein, die Probe
    holt die Konfiguration also neu, sobald z. B. der iperf3-Server gestartet wird.
    """
    peer = wachtl_measure.peer_host(host)
    payload = {
        "probe_id": probe.id,
        "checks": [wachtl_measure.config_check(db, probe, c, peer) for c in checks_for_probe(db, probe)],
        # Gegenstelle für Tests anderer Probes (iperf3-Server und RTP-Reflektor auf dem PC)
        "iperf_server": {"enabled": probe.peer_enabled, "port": wachtl_measure.PROBE_IPERF_PORT},
        "rtp_reflector": {"enabled": probe.peer_enabled, "port": wachtl_measure.PROBE_RTP_PORT},
        "server_urls": settings.wachtl_server_url_list,
        "upload_interval_s": settings.wachtl_upload_interval_s,
        "heartbeat_interval_s": settings.wachtl_heartbeat_interval_s,
        "config_poll_s": settings.wachtl_config_poll_s,
        "client_findings": {
            "enabled": settings.wachtl_client_findings_enabled,
            "notify": settings.wachtl_client_findings_enabled and settings.wachtl_client_findings_notify,
        },
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
    measured = wachtl_measure.matrix_cells(db, checks)
    out_sites = []
    for s in sites:
        site_checks = [c for c in checks if c.enabled and c.site_id in (None, s.id)]
        out_probes = []
        for p in (p for p in probes if p.site_id == s.id):
            online = probe_online(p, now)
            cells = []
            for c in site_checks:
                if c.type != "ping":
                    m = measured.get((p.id, c.id))
                    cells.append({"check_id": c.id, "state": m["state"] if m else "stale",
                                  "text": m["text"] if m else None, "loss_pct": None, "rtt_avg": None,
                                  "jitter_ms": None, "count": 0})
                    continue
                st = stats.get((p.id, c.id))
                state = st["state"] if st else "stale"
                if not online and p.status == "active":
                    state = "stale"
                cells.append({"check_id": c.id, "state": state, "text": None, **({k: st[k] for k in (
                    "loss_pct", "rtt_avg", "jitter_ms", "count")} if st else {
                    "loss_pct": None, "rtt_avg": None, "jitter_ms": None, "count": 0})})
            out_probes.append({"probe_id": p.id, "hostname": p.hostname, "online": online,
                               "status": p.status, "last_seen": p.last_seen, "cells": cells})
        out_sites.append({
            "site_id": s.id, "name": s.name,
            "checks": [{"id": c.id, "name": c.name, "target": c.target, "role": c.role, "type": c.type}
                       for c in site_checks],
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
    wachtl_measure.rollup_and_prune()
    n_rep, n_find = prune_events()
    from app.services import wachtl_notify

    with session_scope() as db:
        wachtl_notify.prune(db)
    if n_rep or n_find:
        log.info("Wachtl: %d Meldungen, %d behobene Hinweise gelöscht", n_rep, n_find)


# --- Störungsmeldungen und Client-Hinweise (Phase 2) -----------------------
def _from_unix(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def store_report(db: Session, probe: WachtlProbe, values: dict) -> tuple[WachtlReport, bool]:
    """Meldung speichern → (Meldung, neu). Dieselbe report_id derselben Probe wird nicht doppelt
    gespeichert."""
    existing = db.execute(select(WachtlReport).where(
        WachtlReport.probe_id == probe.id, WachtlReport.report_id == values["report_id"])
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    report = WachtlReport(
        probe_id=probe.id, site_id=probe.site_id, report_id=values["report_id"],
        occurred_at=_from_unix(values["occurred_at"]), category=values["category"],
        duration=values["duration"],
        description=values["description"] or None, reported_user=values["user"] or None,
        hostname=values["hostname"] or probe.hostname, status="open",
    )
    db.add(report)
    db.flush()
    return report, True


def store_finding(db: Session, probe: WachtlProbe, values: dict) -> bool:
    """Hinweis speichern/aktualisieren → True, wenn neu. Ein behobener Hinweis wird durch eine ältere
    Nachlieferung nicht wieder aktiv (wie Wachtl ``findings.store``)."""
    now = utcnow()
    details = dict(values["details"])
    title = values["title"]
    if values["state"] == "resolved" and details.get("problem"):
        # Beim Beheben schickt die Probe den Entwarnungstext und das ursprüngliche Problem in details.
        details["resolution"] = (values["message"] or title)[:200]
        title = details["problem"][:120]
    existing = db.execute(select(WachtlFinding).where(
        WachtlFinding.probe_id == probe.id, WachtlFinding.finding_id == values["finding_id"])
    ).scalar_one_or_none()
    resolved_at = _from_unix(values["resolved_at"]) if values["resolved_at"] is not None else None
    if existing is not None:
        if existing.state == "resolved" and values["state"] == "active":
            return False
        if values["state"] == "resolved":
            # Problem, Text und Lösungsvorschlag des aktiven Hinweises bleiben stehen
            merged = {**json.loads(existing.details_json or "{}"), **details}
            existing.state = "resolved"
            existing.resolved_at = resolved_at
            existing.details_json = json.dumps(merged, ensure_ascii=False, sort_keys=True)
            existing.updated_at = now
            return False
        target = existing
    else:
        target = WachtlFinding(probe_id=probe.id, site_id=probe.site_id, finding_id=values["finding_id"],
                               received_at=now)
        db.add(target)
    target.kind = values["kind"]
    target.severity = values["severity"]
    target.state = values["state"]
    target.adapter = values["adapter"] or None
    target.adapter_description = values["adapter_description"] or None
    target.title = title
    target.message = values["message"] or None
    target.solution = values["solution"] or None
    target.details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
    target.detected_at = _from_unix(values["detected_at"])
    target.resolved_at = resolved_at
    target.updated_at = now
    db.flush()
    return existing is None


def close_stale_findings(db: Session, probe_id: int, before: datetime, reason: str) -> int:
    """Aktive Hinweise schließen, die die Probe nicht mehr bestätigen kann (Dienst neu gestartet).
    Besteht das Problem weiter, meldet die Probe es mit neuer finding_id erneut."""
    now = utcnow()
    rows = list(db.execute(select(WachtlFinding).where(
        WachtlFinding.probe_id == probe_id, WachtlFinding.state == "active",
        WachtlFinding.detected_at < before)).scalars())
    for f in rows:
        f.state = "resolved"
        f.resolved_at = now
        f.details_json = json.dumps({**json.loads(f.details_json or "{}"), "resolution": reason},
                                    ensure_ascii=False, sort_keys=True)
        f.updated_at = now
    return len(rows)


# --- Ausfallanalyse ---------------------------------------------------------
def incidents(db: Session, since: datetime, until: datetime, site_id: int | None = None) -> list[dict]:
    """Störungen mit Ursache je Standort aus den 1-min-Buckets (``wachtl_analysis.detect_incidents``)."""
    q_sites = select(WachtlSite)
    if site_id is not None:
        q_sites = q_sites.where(WachtlSite.id == site_id)
    sites = list(db.execute(q_sites).scalars())
    checks = [c for c in db.execute(select(WachtlCheck)).scalars() if c.enabled and c.type == "ping"]
    probes = list(db.execute(select(WachtlProbe)).scalars())
    out: list[dict] = []
    for s in sites:
        site_probes = [p.id for p in probes if p.site_id == s.id]
        site_checks = {c.id: c.role for c in checks if c.site_id in (None, s.id)}
        if not site_probes or not site_checks:
            continue
        rows = db.execute(
            select(WachtlSample.probe_id, WachtlSample.check_id, WachtlSample.bucket_ts,
                   WachtlSample.count, WachtlSample.loss)
            .where(WachtlSample.probe_id.in_(site_probes), WachtlSample.step_s == BUCKET_S,
                   WachtlSample.bucket_ts >= since, WachtlSample.bucket_ts < until)
        ).all()
        buckets = {(pid, cid, int(bts.timestamp())): (cnt, loss) for pid, cid, bts, cnt, loss in rows}
        min_interval = min((c.interval_s for c in checks if c.id in site_checks), default=60)
        for inc in wachtl_analysis.detect_incidents(
            s.id, site_probes, site_checks, buckets,
            threshold_pct=settings.wachtl_outage_loss_pct, min_interval_s=min_interval,
        ):
            inc["site_name"] = s.name
            out.append(inc)
    out.sort(key=lambda i: i["start"], reverse=True)
    return out


# --- Diagnose-Auslöser ------------------------------------------------------
_diag_lock = threading.Lock()
_diag_last: dict[int, float] = {}       # site_id → Zeitpunkt des letzten automatischen Laufs (monotonic)


class DiagnoseBusy(RuntimeError):
    pass


def diagnose_targets(db: Session, probe: WachtlProbe) -> list[dict]:
    """Ziele für einen Diagnose-Lauf von netprobe aus: Internet-Referenzen und Ziele des Standorts
    (``gateway`` ist das Gateway des PCs und von hier nicht sinnvoll messbar) plus der PC selbst.

    Für den PC zählt zuerst die LAN-Adresse aus dem Heartbeat (``last_ip`` kann die NAT-Adresse des
    Standort-Routers sein), sonst ``last_ip``."""
    targets: list[dict] = []
    for c in checks_for_probe(db, probe):
        if c.type not in ("ping", "path") or c.target == "gateway" or c.role == "gateway":
            continue
        if all(t["host"] != c.target for t in targets):
            targets.append({"name": c.name, "host": c.target})
    try:
        reported = [str(a) for a in json.loads(probe.addresses_json or "[]")]
    except (json.JSONDecodeError, TypeError):
        reported = []
    for candidate in [*reported, probe.last_ip or ""]:
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            targets.insert(0, {"name": f"PC {probe.hostname}", "host": str(ip)})
            break
    return targets[: settings.diagnostics_max_targets]


def start_diagnostics(probe_id: int, *, report_id: int | None = None, automatic: bool = False) -> None:
    """Diagnose-Lauf im Hintergrund starten; bei ``report_id`` wird der Lauf an der Meldung vermerkt.

    Automatische Läufe höchstens einmal je Standort und ``wachtl_diagnostics_cooldown_min``
    (sonst ``DiagnoseBusy``). Der Lauf selbst geht über ``diagnostics_service.run_diagnostics``.
    """
    with session_scope() as db:
        probe = db.get(WachtlProbe, probe_id)
        if probe is None:
            raise ValueError("Probe nicht gefunden")
        site_id = probe.site_id
        targets = diagnose_targets(db, probe)
    if not targets:
        raise ValueError("Keine Ziele für eine Diagnose – der Probe sind keine Checks zugewiesen")
    cooldown_s = settings.wachtl_diagnostics_cooldown_min * 60
    with _diag_lock:
        last = _diag_last.get(site_id)
        if automatic and last is not None and time.monotonic() - last < cooldown_s:
            raise DiagnoseBusy("Für diesen Standort lief kürzlich schon eine Diagnose")
        _diag_last[site_id] = time.monotonic()
    threading.Thread(target=_diagnose_job, args=(probe_id, targets, report_id),
                     name=f"wachtl-diagnose-{probe_id}", daemon=True).start()


def _diagnose_job(probe_id: int, targets: list[dict], report_id: int | None) -> None:
    from app.services import capture_service, diagnostics_service

    try:
        run = diagnostics_service.run_diagnostics(
            targets=targets, tests={"ping": True, "mtu": True, "traceroute": True},
            interface=capture_service.resolve_default_interface(), duration_s=10,
        )
    except Exception:  # noqa: BLE001 – Hintergrund-Thread: nur protokollieren
        log.exception("Diagnose für Probe %s fehlgeschlagen", probe_id)
        return
    log.info("Diagnose %s für Probe %s fertig: %s", run.id, probe_id, run.overall_status)
    if report_id is not None:
        with session_scope() as db:
            r = db.get(WachtlReport, report_id)
            if r is not None:
                r.diagnostic_run_id = run.id


def prune_events() -> tuple[int, int]:
    cutoff = utcnow() - timedelta(days=settings.wachtl_event_retention_days)
    with session_scope() as db:
        n_r = db.query(WachtlReport).filter(
            WachtlReport.occurred_at < cutoff).delete(synchronize_session=False)
        n_f = db.query(WachtlFinding).filter(
            WachtlFinding.state == "resolved", WachtlFinding.resolved_at < cutoff
        ).delete(synchronize_session=False)
    return n_r, n_f
