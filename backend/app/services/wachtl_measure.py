"""Wachtl-Clients Phase 3: Pfad, Bandbreite und Sprache – Konfiguration, Speichern, Abfragen.

Ergänzt ``wachtl_service`` (Ping/Grundbetrieb). Reine Prüf- und Bewertungslogik steht in
``wachtl_results`` und ``wachtl_checktypes``; hier nur DB-Zugriffe und das Zusammensetzen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.wachtl import (
    WachtlBandwidthRun,
    WachtlCheck,
    WachtlCommand,
    WachtlEvent,
    WachtlHopSample,
    WachtlPathRun,
    WachtlProbe,
    WachtlRtpRun,
)
from app.services import wachtl_checktypes as ct
from app.services import wachtl_results as wr
from app.services.wachtl_ingest import BUCKET_S, Bucket, merge_bucket

log = get_logger("netprobe.wachtl")

HOUR_S = 3600
# Ports, auf denen Probes als Gegenstelle für andere Probes lauschen (Standard der Wachtl-Probe)
PROBE_IPERF_PORT = 5201
PROBE_RTP_PORT = 5004
COMMAND_TYPES = ("run_check", "retrace")
COMMAND_TTL = timedelta(hours=1)


def params_of(check: WachtlCheck) -> dict:
    try:
        stored = json.loads(check.params_json or "{}")
    except json.JSONDecodeError:
        stored = {}
    return ct.merged(check.type, stored)


# --- Konfiguration ----------------------------------------------------------
def _server_peer(kind: str, host: str) -> tuple[str | None, int | None, str | None]:
    if kind == "iperf":
        from app.services.iperf_service import iperf3_server

        if not iperf3_server.is_running():
            return None, None, "iperf3-Server auf netprobe ist aus (Seite iperf3 → Server starten)"
        return host, iperf3_server.port, None
    from app.services.rtp_reflector import rtp_reflector

    if not rtp_reflector.running or rtp_reflector.port is None:
        return None, None, "RTP-Reflektor auf netprobe ist aus (Clients → Einrichtung)"
    return host, rtp_reflector.port, None


def _probe_peer(db: Session, kind: str, peer_id: int) -> tuple[str | None, int | None, str | None]:
    peer = db.get(WachtlProbe, peer_id)
    what = "iperf3-Gegenstelle" if kind == "iperf" else "Gegenstelle"
    if peer is None or peer.status != "active":
        return None, None, f"{what}: Probe nicht vorhanden oder deaktiviert"
    if not peer.peer_enabled:
        return None, None, f"{what}: Probe {peer.hostname} ist nicht als Gegenstelle freigegeben"
    try:
        addresses = [str(a) for a in json.loads(peer.addresses_json or "[]")]
    except json.JSONDecodeError:
        addresses = []
    if not addresses:
        return None, None, f"{what}: Adresse von {peer.hostname} unbekannt (noch kein Heartbeat)"
    return addresses[0], PROBE_IPERF_PORT if kind == "iperf" else PROBE_RTP_PORT, None


def config_check(db: Session, probe: WachtlProbe, c: WachtlCheck, host: str) -> dict:
    """Ein Check im Format von Wachtl ``/config`` (mit Gegenstelle bei geplanten Messungen)."""
    params = params_of(c)
    item: dict = {
        "id": c.id,
        "type": c.type,
        "target": c.target,
        "role": c.role,
        "interval_s": c.interval_s,
        "timeout_ms": c.timeout_ms,
        "params": params,
    }
    if c.type in ("ping", "path"):
        item["timeout_ms"] = min(c.timeout_ms, c.interval_s * 1000)
        return item
    params["offset_min"] = ct.schedule_offset(probe.id, c.id, params["schedule_min"], params["offset_min"])
    peer: tuple[str | None, int | None, str | None] = (None, None, None)
    if c.type in ct.PEER_TYPES and params["peer"] == "server":
        peer = _server_peer(c.type, host)
    elif c.type in ct.PEER_TYPES and params["peer"] == "probe":
        peer = _probe_peer(db, c.type, int(params["peer_probe_id"]))
    elif c.type in ct.PEER_TYPES or params.get("method") == "iperf":
        peer = (params["host"], int(params["port"]), None)
    item["peer"] = {"host": peer[0], "port": peer[1], "error": peer[2]}
    return item


def peer_host(request_host: str | None) -> str:
    """Adresse, unter der Probes netprobe für iperf3/RTP erreichen."""
    return settings.wachtl_peer_host.strip() or (request_host or "")


# --- Ingest: Hop-Pings ------------------------------------------------------
def store_hop_buckets(db: Session, probe_id: int, buckets: dict[tuple[int, int, str, int], Bucket]) -> None:
    if not buckets:
        return
    keys = {(cid, hop, ip, datetime.fromtimestamp(ts, UTC)): b for (cid, hop, ip, ts), b in buckets.items()}
    stamps = [k[3] for k in keys]
    existing = {
        (r.check_id, r.hop, r.hop_ip, r.bucket_ts): r
        for r in db.execute(
            select(WachtlHopSample).where(
                WachtlHopSample.probe_id == probe_id,
                WachtlHopSample.step_s == BUCKET_S,
                WachtlHopSample.check_id.in_({k[0] for k in keys}),
                WachtlHopSample.bucket_ts >= min(stamps),
                WachtlHopSample.bucket_ts <= max(stamps),
            )
        ).scalars()
    }
    for key, b in keys.items():
        row = existing.get(key)
        if row is None:
            row = WachtlHopSample(
                probe_id=probe_id,
                check_id=key[0],
                hop=key[1],
                hop_ip=key[2],
                bucket_ts=key[3],
                step_s=BUCKET_S,
                count=0,
                loss=0,
                rtt_sum=0.0,
                rtt_n=0,
                jitter_sum=0.0,
                jitter_n=0,
            )
            db.add(row)
        merge_bucket(row, b)


# --- Ergebnisse -------------------------------------------------------------
def own_check(db: Session, probe: WachtlProbe, check_id, types: tuple[str, ...]) -> WachtlCheck | None:
    from app.services.wachtl_service import checks_for_probe

    try:
        cid = int(check_id)
    except (TypeError, ValueError):
        return None
    return next((c for c in checks_for_probe(db, probe) if c.id == cid and c.type in types), None)


def add_events(
    db: Session,
    probe: WachtlProbe,
    check: WachtlCheck | None,
    events: list[wr.Event],
    ts: datetime | None = None,
) -> None:
    for e in events:
        db.add(
            WachtlEvent(
                ts=ts or utcnow(),
                site_id=probe.site_id,
                probe_id=probe.id,
                check_id=check.id if check else None,
                kind=e.kind,
                severity=e.severity,
                title=f"{probe.hostname}: {e.title}"[:200],
                detail=e.detail or None,
            )
        )
        log.info("Ereignis %s (%s) – %s: %s", e.kind, e.severity, probe.hostname, e.title)


def store_path(db: Session, probe: WachtlProbe, check: WachtlCheck, data: dict, now: int) -> bool:
    """Pfad speichern; gleicher Pfad schreibt die letzte Zeile fort. → geändert?"""
    hops = wr.hop_list(data.get("hops"))
    if not hops:
        raise wr.ResultError("hops fehlen")
    ts = datetime.fromtimestamp(wr.result_ts(data.get("ts"), now), UTC)
    reached = bool(data.get("reached"))
    reason = str(data.get("reason") or "")[:40] or None
    prev = db.execute(
        select(WachtlPathRun)
        .where(WachtlPathRun.probe_id == probe.id, WachtlPathRun.check_id == check.id)
        .order_by(WachtlPathRun.ts.desc(), WachtlPathRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    new_hash = wr.path_hash(hops)
    prev_hops = json.loads(prev.hops_json) if prev is not None else []
    changed = prev is not None and prev.path_hash != new_hash and wr.path_differs(prev_hops, hops)
    if prev is not None and not changed:
        hops = wr.fill_known(prev_hops, hops)
        prev.ts, prev.reached, prev.reason = ts, reached, reason
        prev.hops_json, prev.path_hash = json.dumps(hops), wr.path_hash(hops)
    else:
        db.add(
            WachtlPathRun(
                probe_id=probe.id,
                check_id=check.id,
                first_ts=ts,
                ts=ts,
                reason=reason,
                reached=reached,
                hops_json=json.dumps(hops),
                path_hash=new_hash,
                changed=changed,
            )
        )
    add_events(
        db,
        probe,
        check,
        wr.path_events(check.target, changed, reached, prev.reached if prev is not None else None),
        ts,
    )
    return changed


def store_bandwidth(db: Session, probe: WachtlProbe, check: WachtlCheck, data: dict, now: int) -> int:
    trigger, results = wr.validate_bandwidth(data)
    ts = datetime.fromtimestamp(wr.result_ts(data.get("ts"), now), UTC)
    for r in results:
        db.add(
            WachtlBandwidthRun(
                probe_id=probe.id, check_id=check.id, ts=ts, kind=check.type, trigger=trigger, **r
            )
        )
    add_events(db, probe, check, wr.bandwidth_events(check.name, params_of(check), results), ts)
    return len(results)


def store_rtp(db: Session, probe: WachtlProbe, check: WachtlCheck, data: dict, now: int) -> None:
    trigger, values = wr.validate_rtp(data)
    ts = datetime.fromtimestamp(wr.result_ts(data.get("ts"), now), UTC)
    db.add(WachtlRtpRun(probe_id=probe.id, check_id=check.id, ts=ts, trigger=trigger, **values))
    add_events(db, probe, check, wr.rtp_events(check.name, params_of(check), values), ts)


# --- Aufträge ---------------------------------------------------------------
def queue_command(db: Session, probe_id: int, ctype: str, check_id: int, username: str) -> WachtlCommand:
    if ctype not in COMMAND_TYPES:
        raise ValueError(ctype)
    pending = db.execute(
        select(WachtlCommand).where(
            WachtlCommand.probe_id == probe_id,
            WachtlCommand.type == ctype,
            WachtlCommand.check_id == check_id,
            WachtlCommand.delivered_at.is_(None),
        )
    ).scalar_one_or_none()
    if pending is not None:
        return pending
    cmd = WachtlCommand(probe_id=probe_id, type=ctype, check_id=check_id, created_by=username)
    db.add(cmd)
    db.flush()
    return cmd


def take_commands(db: Session, probe_id: int) -> list[dict]:
    """Offene Aufträge abholen und als zugestellt markieren (veraltete werden verworfen)."""
    now = utcnow()
    rows = list(
        db.execute(
            select(WachtlCommand)
            .where(
                WachtlCommand.probe_id == probe_id,
                WachtlCommand.delivered_at.is_(None),
                WachtlCommand.created_at > now - COMMAND_TTL,
            )
            .order_by(WachtlCommand.id)
        ).scalars()
    )
    for r in rows:
        r.delivered_at = now
    return [{"id": r.id, "type": r.type, "check_id": r.check_id} for r in rows]


# --- Abfragen ---------------------------------------------------------------
def hop_table(db: Session, probe_id: int, check_id: int, since: datetime, until: datetime) -> list[dict]:
    """MTR-Tabelle: je Hop die Router-Adresse mit den meisten Messungen, bewertet (``evaluate_hops``)."""
    rows = db.execute(
        select(
            WachtlHopSample.hop,
            WachtlHopSample.hop_ip,
            func.sum(WachtlHopSample.count),
            func.sum(WachtlHopSample.loss),
            func.sum(WachtlHopSample.rtt_sum),
            func.sum(WachtlHopSample.rtt_n),
            func.min(WachtlHopSample.rtt_min),
            func.max(WachtlHopSample.rtt_max),
            func.sum(WachtlHopSample.jitter_sum),
            func.sum(WachtlHopSample.jitter_n),
        )
        .where(
            WachtlHopSample.probe_id == probe_id,
            WachtlHopSample.check_id == check_id,
            WachtlHopSample.bucket_ts >= since,
            WachtlHopSample.bucket_ts <= until,
        )
        .group_by(WachtlHopSample.hop, WachtlHopSample.hop_ip)
    ).all()
    best: dict[int, dict] = {}
    for hop, ip, cnt, loss, rsum, rn, rmin, rmax, jsum, jn in rows:
        item = {
            "hop": hop,
            "ip": ip,
            "count": int(cnt or 0),
            "loss": int(loss or 0),
            "rtt_avg": round(rsum / rn, 2) if rn else None,
            "rtt_min": rmin,
            "rtt_max": rmax,
            "jitter_ms": round(jsum / jn, 2) if jn else None,
            "alt_ips": [],
        }
        cur = best.get(hop)
        if cur is None or item["count"] > cur["count"]:
            if cur is not None:
                item["alt_ips"] = [*cur["alt_ips"], cur["ip"]]
            best[hop] = item
        else:
            cur["alt_ips"].append(ip)
    return wr.evaluate_hops([best[h] for h in sorted(best)])


def path_runs(db: Session, probe_id: int, check_id: int, limit: int = 50) -> list[WachtlPathRun]:
    return list(
        db.execute(
            select(WachtlPathRun)
            .where(WachtlPathRun.probe_id == probe_id, WachtlPathRun.check_id == check_id)
            .order_by(WachtlPathRun.ts.desc(), WachtlPathRun.id.desc())
            .limit(limit)
        ).scalars()
    )


def bandwidth_runs(
    db: Session, probe_id: int, check_id: int, since: datetime | None, until: datetime | None
) -> list[WachtlBandwidthRun]:
    q = select(WachtlBandwidthRun).where(
        WachtlBandwidthRun.probe_id == probe_id, WachtlBandwidthRun.check_id == check_id
    )
    if since is not None:
        q = q.where(WachtlBandwidthRun.ts >= since)
    if until is not None:
        q = q.where(WachtlBandwidthRun.ts <= until)
    return list(db.execute(q.order_by(WachtlBandwidthRun.ts.desc()).limit(2000)).scalars())


def rtp_runs(
    db: Session, probe_id: int, check_id: int, since: datetime | None, until: datetime | None
) -> list[WachtlRtpRun]:
    q = select(WachtlRtpRun).where(WachtlRtpRun.probe_id == probe_id, WachtlRtpRun.check_id == check_id)
    if since is not None:
        q = q.where(WachtlRtpRun.ts >= since)
    if until is not None:
        q = q.where(WachtlRtpRun.ts <= until)
    return list(db.execute(q.order_by(WachtlRtpRun.ts.desc()).limit(2000)).scalars())


def _latest(db: Session, model, since: datetime) -> dict[tuple[int, int], list]:
    """Läufe der letzten Messung je (Probe, Check) seit ``since``."""
    last = {
        (p, c): ts
        for p, c, ts in db.execute(
            select(model.probe_id, model.check_id, func.max(model.ts))
            .where(model.ts >= since)
            .group_by(model.probe_id, model.check_id)
        ).all()
    }
    out: dict[tuple[int, int], list] = {}
    if not last:
        return out
    for r in db.execute(select(model).where(model.ts >= min(last.values()))).scalars():
        if last.get((r.probe_id, r.check_id)) == r.ts:
            out.setdefault((r.probe_id, r.check_id), []).append(r)
    return out


def matrix_cells(db: Session, checks: list[WachtlCheck]) -> dict[tuple[int, int], dict]:
    """Zellen der Standort-Matrix für Pfad, Bandbreite und Sprache (letzte Messung der letzten 7 Tage)."""
    since = utcnow() - timedelta(days=7)
    by_id = {c.id: c for c in checks}
    cells: dict[tuple[int, int], dict] = {}
    for key, runs in _latest(db, WachtlPathRun, since).items():
        r = runs[0]
        hops = json.loads(r.hops_json)
        cells[key] = {
            "state": "ok" if r.reached else "warn",
            "text": f"{'erreicht' if r.reached else 'nicht erreicht'} · {len(hops)} Hops",
            "ts": r.ts,
        }
    for key, runs in _latest(db, WachtlBandwidthRun, since).items():
        c = by_id.get(key[1])
        params = params_of(c) if c else {}
        parts, state = [], "ok"
        for r in sorted(runs, key=lambda x: x.direction):
            arrow = "↓" if r.direction == "down" else "↑"
            if r.error or r.mbps is None:
                state = "down"
                parts.append(f"{arrow} Fehler")
                continue
            limit = float(params.get(f"min_{r.direction}_mbps") or 0)
            if limit and r.mbps < limit:
                state = "warn" if state == "ok" else state
            parts.append(f"{arrow} {r.mbps:.0f}")
        cells[key] = {"state": state, "text": " ".join(parts) + " Mbit/s", "ts": runs[0].ts}
    for key, runs in _latest(db, WachtlRtpRun, since).items():
        r = runs[0]
        c = by_id.get(key[1])
        limit = float(params_of(c).get("min_mos") or 0) if c else 0
        if r.error or r.mos is None:
            cells[key] = {"state": "down", "text": "Fehler", "ts": r.ts}
        else:
            state = "warn" if limit and r.mos < limit else "ok"
            cells[key] = {"state": state, "text": f"MOS {r.mos:.2f}", "ts": r.ts}
    return cells


# --- Verdichtung und Aufbewahrung -------------------------------------------
def rollup_and_prune() -> None:
    """Hop-Pings wie die Ping-Buckets nach ``wachtl_minute_retention_days`` zu Stunden zusammenfassen;
    alte Läufe, Ereignisse und zugestellte Aufträge löschen."""
    from app.services.wachtl_service import _epoch_bucket

    now = utcnow()
    cutoff_epoch = int((now - timedelta(days=settings.wachtl_minute_retention_days)).timestamp())
    cutoff = datetime.fromtimestamp(cutoff_epoch - cutoff_epoch % HOUR_S, UTC)
    with session_scope() as db:
        hb = _epoch_bucket(WachtlHopSample.bucket_ts, HOUR_S).label("hb")
        old = WachtlHopSample.step_s == BUCKET_S, WachtlHopSample.bucket_ts < cutoff
        groups = db.execute(
            select(
                WachtlHopSample.probe_id,
                WachtlHopSample.check_id,
                WachtlHopSample.hop,
                WachtlHopSample.hop_ip,
                hb,
                func.sum(WachtlHopSample.count),
                func.sum(WachtlHopSample.loss),
                func.min(WachtlHopSample.rtt_min),
                func.max(WachtlHopSample.rtt_max),
                func.sum(WachtlHopSample.rtt_sum),
                func.sum(WachtlHopSample.rtt_n),
                func.sum(WachtlHopSample.jitter_sum),
                func.sum(WachtlHopSample.jitter_n),
            )
            .where(*old)
            .group_by(
                WachtlHopSample.probe_id,
                WachtlHopSample.check_id,
                WachtlHopSample.hop,
                WachtlHopSample.hop_ip,
                hb,
            )
        ).all()
        for pid, cid, hop, ip, hts, cnt, loss, rmin, rmax, rsum, rn, jsum, jn in groups:
            bts = datetime.fromtimestamp(int(hts), UTC)
            row = db.execute(
                select(WachtlHopSample).where(
                    WachtlHopSample.probe_id == pid,
                    WachtlHopSample.check_id == cid,
                    WachtlHopSample.hop == hop,
                    WachtlHopSample.hop_ip == ip,
                    WachtlHopSample.step_s == HOUR_S,
                    WachtlHopSample.bucket_ts == bts,
                )
            ).scalar_one_or_none()
            if row is None:
                row = WachtlHopSample(
                    probe_id=pid,
                    check_id=cid,
                    hop=hop,
                    hop_ip=ip,
                    bucket_ts=bts,
                    step_s=HOUR_S,
                    count=0,
                    loss=0,
                    rtt_sum=0.0,
                    rtt_n=0,
                    jitter_sum=0.0,
                    jitter_n=0,
                )
                db.add(row)
            merge_bucket(
                row,
                Bucket(
                    count=int(cnt or 0),
                    loss=int(loss or 0),
                    rtt_min=rmin,
                    rtt_max=rmax,
                    rtt_sum=float(rsum or 0),
                    rtt_n=int(rn or 0),
                    jitter_sum=float(jsum or 0),
                    jitter_n=int(jn or 0),
                ),
            )
        db.flush()
        db.query(WachtlHopSample).filter(*old).delete(synchronize_session=False)
        db.query(WachtlHopSample).filter(
            WachtlHopSample.bucket_ts < now - timedelta(days=settings.wachtl_retention_days)
        ).delete(synchronize_session=False)
        result_cutoff = now - timedelta(days=settings.wachtl_result_retention_days)
        for model in (WachtlPathRun, WachtlBandwidthRun, WachtlRtpRun):
            db.query(model).filter(model.ts < result_cutoff).delete(synchronize_session=False)
        db.query(WachtlEvent).filter(
            WachtlEvent.ts < now - timedelta(days=settings.wachtl_event_retention_days)
        ).delete(synchronize_session=False)
        db.query(WachtlCommand).filter(WachtlCommand.created_at < now - timedelta(days=7)).delete(
            synchronize_session=False
        )
        if groups:
            log.info("Wachtl: %d Hop-Stunden-Buckets verdichtet", len(groups))
