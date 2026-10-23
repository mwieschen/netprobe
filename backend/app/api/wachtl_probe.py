"""Wachtl-Probe-API v1 (``/api/probe/v1``): Gegenseite für unveränderte Wachtl-Windows-Probes.

Vertrag wie Wachtl 0.9 (``server/wachtl/api_probe.py``): Enrollment mit Token, Konfiguration mit
ETag, Ingest im Prometheus-Textformat (idempotent per ``X-Batch-Id``), Heartbeat, Abmeldung.
Fehler antworten wie Wachtl mit ``{"error": "…"}``. Ping-Checks, Störungsmeldungen (``/report``) und
Client-Hinweise (``/findings``); ``/results/*`` folgen (docs/wachtl-zentrale.md).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import client_ip
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import RateLimiter
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.wachtl import WachtlEnrollToken, WachtlIngestBatch, WachtlProbe, WachtlSite
from app.services import audit, wachtl_events, wachtl_service
from app.services.wachtl_ingest import (
    BATCH_ID_RE,
    PROBE_HOST_RE,
    BodyTooLarge,
    decompress_gzip,
    parse_ingest,
)

log = get_logger("netprobe.wachtl.api")
router = APIRouter(prefix="/api/probe/v1", tags=["wachtl-probe"])

# Fehlversuche beim Enrollment je Quell-IP (Token-Raten verhindern).
enroll_rate_limiter = RateLimiter(10, 300)


class WachtlApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def wachtl_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, WachtlApiError)
    return JSONResponse({"error": exc.message}, status_code=exc.status)


def require_enabled(db: Session = Depends(get_db)) -> None:
    if not wachtl_service.is_enabled(db):
        raise WachtlApiError(404, "Wachtl-Anbindung ist abgeschaltet")


def current_probe(request: Request, db: Session = Depends(get_db),
                  _: None = Depends(require_enabled)) -> WachtlProbe:
    """Erwartet ``Authorization: Bearer <probe_id>.<secret>``."""
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise WachtlApiError(401, "Token fehlt")
    token = header[7:].strip()
    probe_id, _sep, secret = token.partition(".")
    if not (probe_id.isascii() and probe_id.isdigit()) or not secret or len(probe_id) > 12:
        raise WachtlApiError(401, "Token ungültig")
    probe = db.get(WachtlProbe, int(probe_id))
    if probe is None or not hmac.compare_digest(probe.token_hash, wachtl_service.hash_token(token)):
        raise WachtlApiError(401, "Token ungültig")
    if probe.status != "active":
        raise WachtlApiError(403, "Probe ist deaktiviert")
    return probe


def _addresses(raw) -> list[str]:
    """Lokale IPv4-Adressen der Probe; Link-Local und Loopback entfallen."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:16]:
        try:
            ip = ipaddress.ip_address(str(item))
        except ValueError:
            continue
        if ip.version == 4 and not (ip.is_loopback or ip.is_link_local) and str(ip) not in out:
            out.append(str(ip))
    return out


@router.post("/enroll", status_code=201, dependencies=[Depends(require_enabled)])
def enroll(request: Request, data: Any = Body(default=None), db: Session = Depends(get_db)) -> JSONResponse:
    data = data if isinstance(data, dict) else {}
    ip = client_ip(request)
    if not enroll_rate_limiter.check(f"ip:{ip}"):
        raise WachtlApiError(429, "Zu viele fehlgeschlagene Registrierungen – bitte später erneut versuchen")
    token = str(data.get("enrollment_token", ""))
    hostname = str(data.get("hostname", "")).strip()
    if not token or not PROBE_HOST_RE.match(hostname):
        raise WachtlApiError(400, "enrollment_token und gültiger hostname erforderlich")
    now = utcnow()
    et = db.execute(
        select(WachtlEnrollToken).where(WachtlEnrollToken.token_hash == wachtl_service.hash_token(token))
    ).scalar_one_or_none()
    if et is None or et.revoked or et.expires_at < now or et.uses >= et.max_uses:
        enroll_rate_limiter.hit(f"ip:{ip}")
        log.warning("Enrollment abgelehnt für %s von %s", hostname, ip)
        raise WachtlApiError(403, "Enrollment-Token ungültig, abgelaufen oder verbraucht")

    site = db.get(WachtlSite, et.site_id)
    assert site is not None  # FK mit ON DELETE CASCADE
    existing = db.execute(
        select(WachtlProbe).where(WachtlProbe.site_id == site.id, WachtlProbe.hostname == hostname)
    ).scalar_one_or_none()
    if existing is not None and existing.status == "disabled" and existing.deregistered_at is None:
        # Vom Admin deaktiviert: bleibt gesperrt, auch bei Neuinstallation mit gültigem Token.
        log.warning("Enrollment abgelehnt für deaktivierte Probe %s von %s", hostname, ip)
        raise WachtlApiError(
            403, "Probe ist in netprobe deaktiviert – dort zuerst aktivieren, dann neu installieren")
    os_ = str(data.get("os", ""))[:200]
    version = str(data.get("version", ""))[:50]
    if existing is not None:
        probe = existing
        action = "wachtl_probe_reenroll"
    else:
        probe = WachtlProbe(site_id=site.id, hostname=hostname, token_hash="pending", status="active")
        db.add(probe)
        db.flush()
        action = "wachtl_probe_enroll"
    probe_token = f"{probe.id}.{wachtl_service.new_secret()}"
    probe.token_hash = wachtl_service.hash_token(probe_token)
    probe.status = "active"
    probe.deregistered_at = None
    probe.os = os_
    probe.version = version
    probe.last_ip = ip
    et.uses += 1
    audit.record(db, action, username=f"probe:{hostname}",
                 detail=f"Probe {probe.id}, Standort {site.name}", ip=ip)
    db.commit()
    return JSONResponse({"probe_id": probe.id, "token": probe_token, "site_name": site.name}, status_code=201)


@router.get("/config")
def config(request: Request, db: Session = Depends(get_db),
           probe: WachtlProbe = Depends(current_probe)) -> Response:
    payload, etag = wachtl_service.config_payload(db, probe)
    if request.headers.get("if-none-match", "").strip().strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    return JSONResponse(payload, headers={"ETag": f'"{etag}"'})


def _ingest(probe_id: int, full_id: str, body: str, ip: str) -> JSONResponse | Response:
    with session_scope() as db:
        if wachtl_service.batch_seen(db, full_id):
            return Response(status_code=204)
        probe = db.get(WachtlProbe, probe_id)
        assert probe is not None
        check_ids = {c.id for c in wachtl_service.checks_for_probe(db, probe)}
        result = parse_ingest(body, check_ids, int(time.time() * 1000))
        if result.rejected:
            log.info("Probe %s: %d Zeilen verworfen %s", probe_id, result.rejected, result.reasons)
        wachtl_service.store_buckets(db, probe_id, result.buckets)
        db.add(WachtlIngestBatch(batch_id=full_id, probe_id=probe_id))
        probe.last_seen = utcnow()
        probe.last_ip = ip
    if result.rejected and not result.accepted:
        return JSONResponse({"accepted": 0, "rejected": result.rejected, "reasons": result.reasons},
                            status_code=422)
    return Response(status_code=204)


@router.post("/ingest")
async def ingest(request: Request, probe: WachtlProbe = Depends(current_probe)) -> Response:
    batch_id = request.headers.get("x-batch-id", "")
    if not BATCH_ID_RE.match(batch_id):
        raise WachtlApiError(400, "X-Batch-Id fehlt oder ungültig")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > settings.wachtl_max_body_bytes:
        raise WachtlApiError(413, "Batch zu groß")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > settings.wachtl_max_body_bytes:
            raise WachtlApiError(413, "Batch zu groß")
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            raw = decompress_gzip(raw, settings.wachtl_max_ingest_bytes)
        except BodyTooLarge as exc:
            raise WachtlApiError(413, "Batch zu groß") from exc
        except Exception as exc:  # noqa: BLE001 – zlib.error, EOFError …
            raise WachtlApiError(400, "gzip-Daten ungültig") from exc
    elif len(raw) > settings.wachtl_max_ingest_bytes:
        raise WachtlApiError(413, "Batch zu groß")
    body = raw.decode("utf-8", errors="replace")
    return await run_in_threadpool(_ingest, probe.id, f"{probe.id}:{batch_id}", body, client_ip(request))


@router.post("/heartbeat")
def heartbeat(request: Request, data: Any = Body(default=None), db: Session = Depends(get_db),
              probe: WachtlProbe = Depends(current_probe)) -> JSONResponse:
    data = data if isinstance(data, dict) else {}

    def _int(key: str) -> int:
        try:
            return max(0, min(int(data.get(key) or 0), 2**31 - 1))
        except (TypeError, ValueError):
            return 0

    uptime = _int("uptime_s")
    if probe.uptime_s and uptime < probe.uptime_s:
        # Dienst neu gestartet: ältere aktive Hinweise kann die Probe nicht mehr auflösen
        wachtl_service.close_stale_findings(db, probe.id, utcnow() - timedelta(seconds=uptime),
                                            "Nicht mehr bestätigt – Wachtl-Dienst neu gestartet")
    probe.last_seen = utcnow()
    probe.last_ip = client_ip(request)
    probe.version = str(data.get("version", probe.version or ""))[:50]
    probe.os = str(data.get("os", probe.os or ""))[:200]
    probe.buffer_items = _int("buffer_items")
    probe.uptime_s = uptime
    probe.addresses_json = json.dumps(_addresses(data.get("addresses")))
    _, etag = wachtl_service.config_payload(db, probe)
    db.commit()
    # Aufträge ("Jetzt messen") gibt es in Phase 1 noch nicht.
    return JSONResponse({"config_etag": etag, "server_time": int(time.time()), "commands": []})


@router.post("/deregister", status_code=204)
def deregister(request: Request, db: Session = Depends(get_db),
               probe: WachtlProbe = Depends(current_probe)) -> Response:
    """Abmeldung durch den Deinstaller: Probe deaktivieren, Token sofort ungültig.
    Messwerte bleiben bis zum Ablauf der Aufbewahrung erhalten."""
    probe.status = "disabled"
    probe.token_hash = "deregistered:" + wachtl_service.new_secret(16)
    probe.deregistered_at = utcnow()
    audit.record(db, "wachtl_probe_deregister", username=f"probe:{probe.hostname}",
                 detail=f"Probe {probe.id}, Deinstallation", ip=client_ip(request))
    db.commit()
    log.info("Probe %s (%s) hat sich abgemeldet", probe.id, probe.hostname)
    return Response(status_code=204)


@router.post("/report")
def report(request: Request, data: Any = Body(default=None), db: Session = Depends(get_db),
           probe: WachtlProbe = Depends(current_probe)) -> JSONResponse:
    """Störungsmeldung eines Benutzers (Tray-Symbol), zugestellt über den lokalen Wachtl-Dienst.

    201 = neu gespeichert, 200 = dieselbe report_id gab es schon (Nachlieferung). Eine neue Meldung
    startet optional einen netprobe-Diagnose-Lauf (``wachtl_report_diagnostics``).
    """
    try:
        values = wachtl_events.validate_report(data, int(time.time()))
    except wachtl_events.EventError as exc:
        raise WachtlApiError(400, str(exc)) from exc
    rep, created = wachtl_service.store_report(db, probe, values)
    db.commit()
    if created:
        log.info("Probe %s: Störungsmeldung %s (%s) von %s", probe.hostname, rep.id, values["category"],
                 values["user"] or "unbekannt")
        if settings.wachtl_report_diagnostics:
            try:
                wachtl_service.start_diagnostics(probe.id, report_id=rep.id, automatic=True)
            except (wachtl_service.DiagnoseBusy, ValueError) as exc:
                log.info("Keine automatische Diagnose für Meldung %s: %s", rep.id, exc)
    return JSONResponse({"id": rep.id}, status_code=201 if created else 200)


@router.post("/findings")
def findings(data: Any = Body(default=None), db: Session = Depends(get_db),
             probe: WachtlProbe = Depends(current_probe)) -> JSONResponse:
    """Client-Hinweise der Probe (Kabel getrennt, Link-Geschwindigkeit, Treiber …), Upsert je finding_id;
    ungültige Einträge werden übersprungen und in ``errors`` genannt."""
    try:
        items, errors = wachtl_events.validate_findings_batch(data, int(time.time()))
    except wachtl_events.EventError as exc:
        raise WachtlApiError(400, str(exc)) from exc
    new = 0
    for values in items:
        if wachtl_service.store_finding(db, probe, values):
            new += 1
            log.info("Probe %s: Hinweis %s (%s) – %s", probe.hostname, values["kind"], values["severity"],
                     values["title"])
    for e in errors:
        log.warning("Probe %s: Hinweis abgelehnt – %s", probe.hostname, e)
    db.commit()
    return JSONResponse({"stored": len(items), "new": new, "rejected": len(errors), "errors": errors[:10]})
