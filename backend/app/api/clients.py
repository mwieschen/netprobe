"""Verwaltung der Wachtl-Clients (``/api/clients``): Standorte, Enrollment-Token, Checks, Probes,
Standort-Matrix, Zeitreihen, Störungsmeldungen, Client-Hinweise, Ausfälle, Diagnose, Pfad/Bandbreite/Sprache,
Aufträge („Jetzt messen“) und Ereignisse.
Die Probes selbst sprechen ``/api/probe/v1`` (``app.api.wachtl_probe``)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.api.timerange import resolve_range
from app.core.config import settings
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.user import User
from app.models.wachtl import (
    WachtlCheck,
    WachtlEnrollToken,
    WachtlEvent,
    WachtlFinding,
    WachtlPathRun,
    WachtlProbe,
    WachtlReport,
    WachtlSite,
)
from app.schemas.schemas import (
    WachtlBandwidthRunOut,
    WachtlCheckCreate,
    WachtlCheckOut,
    WachtlCheckUpdate,
    WachtlClientSetup,
    WachtlCommandOut,
    WachtlDiagnoseStarted,
    WachtlEventOut,
    WachtlFindingOut,
    WachtlIncidentOut,
    WachtlOverview,
    WachtlPathRunOut,
    WachtlPathView,
    WachtlProbeOut,
    WachtlProbeUpdate,
    WachtlReportOut,
    WachtlReportStatusUpdate,
    WachtlRtpRunOut,
    WachtlSeriesPoint,
    WachtlSettingsOut,
    WachtlSettingsUpdate,
    WachtlSiteCreate,
    WachtlSiteOut,
    WachtlTokenCreate,
    WachtlTokenCreated,
    WachtlTokenOut,
)
from app.services import audit, kvstore, wachtl_events, wachtl_measure, wachtl_service
from app.services import wachtl_checktypes as ct
from app.services.proc import validate_network_target

router = APIRouter(prefix="/api/clients", tags=["clients"])


# --- Einstellungen ----------------------------------------------------------
def _rtp_wanted(db: Session) -> tuple[bool, int]:
    from app.services import rtp_reflector as rr

    return (bool(kvstore.get_value(db, rr.KV_ENABLED, settings.wachtl_rtp_enabled)),
            int(kvstore.get_value(db, rr.KV_PORT, settings.wachtl_rtp_port)))


def _settings_out(db: Session, request: Request) -> WachtlSettingsOut:
    from app.services.iperf_service import iperf3_server
    from app.services.rtp_reflector import rtp_reflector

    pin, pin_error = wachtl_service.tls_pin()
    rtp_enabled, rtp_port = _rtp_wanted(db)
    setup = wachtl_service.client_setup()
    return WachtlSettingsOut(
        enabled=wachtl_service.is_enabled(db),
        pin=pin,
        pin_error=pin_error,
        server_urls=settings.wachtl_server_url_list,
        min_interval_s=settings.wachtl_min_interval_s,
        rtp_enabled=rtp_enabled,
        rtp_port=rtp_port,
        rtp_running=rtp_reflector.running,
        rtp_error=rtp_reflector.error,
        iperf_running=iperf3_server.is_running(),
        iperf_port=iperf3_server.port,
        peer_host=wachtl_measure.peer_host(request.url.hostname),
        setup=WachtlClientSetup(**{k: v for k, v in setup.items() if k != "path"}) if setup else None,
    )


@router.get("/settings", response_model=WachtlSettingsOut)
def get_settings(request: Request, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)) -> WachtlSettingsOut:
    return _settings_out(db, request)


@router.get("/setup")
def download_setup(_: User = Depends(require_admin)) -> FileResponse:
    """Mitgeliefertes, unverändertes Wachtl-Setup für die Verteilung auf die PCs."""
    setup = wachtl_service.client_setup()
    if setup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Kein Wachtl-Setup installiert – netprobe aus dem Release-Paket installieren.")
    return FileResponse(setup["path"], media_type="application/vnd.microsoft.portable-executable",
                        filename=setup["filename"])


@router.put("/settings", response_model=WachtlSettingsOut)
def update_settings(payload: WachtlSettingsUpdate, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_admin)) -> WachtlSettingsOut:
    from app.services import rtp_reflector as rr

    if payload.enabled is not None:
        kvstore.set_value(db, wachtl_service.KV_ENABLED, payload.enabled)
        audit.record(db, "wachtl_enable" if payload.enabled else "wachtl_disable", user_id=user.id,
                     username=user.username, ip=client_ip(request))
    if payload.rtp_enabled is not None:
        kvstore.set_value(db, rr.KV_ENABLED, payload.rtp_enabled)
    if payload.rtp_port is not None:
        kvstore.set_value(db, rr.KV_PORT, payload.rtp_port)
    if payload.rtp_enabled is not None or payload.rtp_port is not None:
        audit.record(db, "wachtl_rtp_settings", user_id=user.id, username=user.username,
                     detail=f"enabled={payload.rtp_enabled} port={payload.rtp_port}", ip=client_ip(request))
    db.commit()
    rtp_enabled, rtp_port = _rtp_wanted(db)
    try:
        rr.rtp_reflector.apply(wachtl_service.is_enabled(db) and rtp_enabled, rtp_port)
    except Exception as exc:  # noqa: BLE001 – Einstellung ist gespeichert, Zustand steht in rtp_error
        rr.rtp_reflector.error = str(exc)
    return _settings_out(db, request)


@router.get("/check-types")
def check_types(_: User = Depends(get_current_user)) -> list[dict]:
    """Check-Typen mit ihren Feldern – Grundlage des Formulars im UI."""
    return ct.describe()


@router.get("/overview", response_model=WachtlOverview)
def overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return wachtl_service.overview(db)


# --- Standorte --------------------------------------------------------------
@router.get("/sites", response_model=list[WachtlSiteOut])
def list_sites(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[WachtlSite]:
    return list(db.execute(select(WachtlSite).order_by(WachtlSite.name)).scalars())


@router.post("/sites", response_model=WachtlSiteOut, status_code=status.HTTP_201_CREATED)
def create_site(payload: WachtlSiteCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_admin)) -> WachtlSite:
    name = payload.name.strip()
    if db.execute(select(WachtlSite).where(WachtlSite.name == name)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Standort mit diesem Namen existiert bereits")
    site = WachtlSite(name=name, description=payload.description)
    db.add(site)
    audit.record(db, "wachtl_site_create", user_id=user.id, username=user.username, detail=name,
                 ip=client_ip(request))
    db.commit()
    db.refresh(site)
    return site


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(site_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_admin)) -> None:
    site = db.get(WachtlSite, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht gefunden")
    if db.execute(select(WachtlProbe.id).where(WachtlProbe.site_id == site_id).limit(1)).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Standort hat noch Probes – diese zuerst löschen")
    db.delete(site)
    audit.record(db, "wachtl_site_delete", user_id=user.id, username=user.username, detail=site.name,
                 ip=client_ip(request))
    db.commit()


# --- Enrollment-Token -------------------------------------------------------
@router.get("/tokens", response_model=list[WachtlTokenOut])
def list_tokens(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[WachtlEnrollToken]:
    return list(db.execute(select(WachtlEnrollToken).order_by(WachtlEnrollToken.id.desc())).scalars())


@router.post("/tokens", response_model=WachtlTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(payload: WachtlTokenCreate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> dict:
    site = db.get(WachtlSite, payload.site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht gefunden")
    token = "wachtl-" + wachtl_service.new_secret(24)
    et = WachtlEnrollToken(
        site_id=site.id, token_hash=wachtl_service.hash_token(token), description=payload.description,
        expires_at=utcnow() + timedelta(days=payload.days), max_uses=payload.max_uses, uses=0,
        revoked=False, created_by=user.username,
    )
    db.add(et)
    audit.record(db, "wachtl_token_create", user_id=user.id, username=user.username,
                 detail=f"Standort {site.name}, {payload.max_uses} Nutzungen, {payload.days} Tage",
                 ip=client_ip(request))
    db.commit()
    db.refresh(et)
    return {**WachtlTokenOut.model_validate(et).model_dump(), "token": token}


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> None:
    et = db.get(WachtlEnrollToken, token_id)
    if et is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token nicht gefunden")
    et.revoked = True
    audit.record(db, "wachtl_token_revoke", user_id=user.id, username=user.username, detail=str(token_id),
                 ip=client_ip(request))
    db.commit()


# --- Checks -----------------------------------------------------------------
def _check_out(c: WachtlCheck) -> WachtlCheckOut:
    params = wachtl_measure.params_of(c)
    return WachtlCheckOut(
        id=c.id, name=c.name, type=c.type, target=c.target, role=c.role, interval_s=c.interval_s,
        timeout_ms=c.timeout_ms, site_id=c.site_id, enabled=c.enabled, created_at=c.created_at, params=params,
        summary=ct.summary(c.type, c.interval_s, params),
    )


def _apply_check(db: Session, check: WachtlCheck, data: dict) -> None:
    """Werte prüfen und übernehmen. Ping/Pfad: Ziel + Intervall; geplante Messungen: Ziel aus den
    Parametern."""
    ctype = check.type
    if data.get("site_id") is not None and db.get(WachtlSite, data["site_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht gefunden")
    for key in ("name", "role", "site_id", "enabled"):
        if key in data and (data[key] is not None or key == "site_id"):
            setattr(check, key, data[key])
    probe_names = {p.id: p.hostname for p in db.execute(select(WachtlProbe)).scalars()}
    raw_params = data["params"] if data.get("params") is not None else wachtl_measure.params_of(check)
    try:
        params = ct.parse_params(ctype, raw_params, probe_ids=set(probe_names))
    except ct.ParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    check.params_json = json.dumps(params)
    if ct.TYPES[ctype].uses_target:
        target = (data.get("target") or check.target or "").strip()
        if not target:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ziel fehlt")
        if target != "gateway" or ctype != "ping":
            try:
                validate_network_target(target)
            except ValueError as exc:
                hint = ("IP-Adresse, Hostname oder 'gateway'" if ctype == "ping"
                        else "IP-Adresse oder Hostname")
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiges Ziel – {hint}") from exc
        check.target = target
        interval = data.get("interval_s") or check.interval_s or settings.wachtl_min_interval_s
        if interval < settings.wachtl_min_interval_s:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Intervall mindestens {settings.wachtl_min_interval_s} s")
        check.interval_s = interval
        check.timeout_ms = data.get("timeout_ms") or check.timeout_ms or 1000
    else:
        check.target = ct.target_for(ctype, params, probe_names)
        check.interval_s = ct.SCHEDULED_INTERVAL_S
        check.timeout_ms = ct.timeout_ms(ctype, params)


@router.get("/checks", response_model=list[WachtlCheckOut])
def list_checks(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[WachtlCheckOut]:
    return [_check_out(c) for c in db.execute(select(WachtlCheck).order_by(WachtlCheck.id)).scalars()]


@router.post("/checks", response_model=WachtlCheckOut, status_code=status.HTTP_201_CREATED)
def create_check(payload: WachtlCheckCreate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> WachtlCheckOut:
    check = WachtlCheck(type=payload.type, target="", interval_s=payload.interval_s,
                        timeout_ms=payload.timeout_ms)
    _apply_check(db, check, payload.model_dump())
    db.add(check)
    audit.record(db, "wachtl_check_create", user_id=user.id, username=user.username,
                 detail=f"{check.type} {check.name} → {check.target}", ip=client_ip(request))
    db.commit()
    db.refresh(check)
    return _check_out(check)


@router.patch("/checks/{check_id}", response_model=WachtlCheckOut)
def update_check(check_id: int, payload: WachtlCheckUpdate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> WachtlCheckOut:
    check = db.get(WachtlCheck, check_id)
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check nicht gefunden")
    _apply_check(db, check, payload.model_dump(exclude_unset=True))
    audit.record(db, "wachtl_check_update", user_id=user.id, username=user.username, detail=str(check_id),
                 ip=client_ip(request))
    db.commit()
    db.refresh(check)
    return _check_out(check)


@router.delete("/checks/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_check(check_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> None:
    check = db.get(WachtlCheck, check_id)
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check nicht gefunden")
    db.delete(check)
    audit.record(db, "wachtl_check_delete", user_id=user.id, username=user.username, detail=str(check_id),
                 ip=client_ip(request))
    db.commit()


# --- Probes -----------------------------------------------------------------
def _probe_out(p: WachtlProbe, site_names: dict[int, str]) -> WachtlProbeOut:
    try:
        addresses = [str(a) for a in json.loads(p.addresses_json or "[]")]
    except json.JSONDecodeError:
        addresses = []
    return WachtlProbeOut(
        id=p.id, site_id=p.site_id, site_name=site_names.get(p.site_id, ""), hostname=p.hostname, os=p.os,
        version=p.version, status=p.status, online=wachtl_service.probe_online(p), created_at=p.created_at,
        last_seen=p.last_seen, last_ip=p.last_ip, buffer_items=p.buffer_items, uptime_s=p.uptime_s,
        addresses=addresses, deregistered_at=p.deregistered_at, peer_enabled=p.peer_enabled,
    )


def _site_names(db: Session) -> dict[int, str]:
    return {s.id: s.name for s in db.execute(select(WachtlSite)).scalars()}


@router.get("/probes", response_model=list[WachtlProbeOut])
def list_probes(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[WachtlProbeOut]:
    names = _site_names(db)
    probes = db.execute(select(WachtlProbe).order_by(WachtlProbe.hostname)).scalars()
    return [_probe_out(p, names) for p in probes]


@router.get("/probes/{probe_id}", response_model=WachtlProbeOut)
def get_probe(probe_id: int, db: Session = Depends(get_db),
              _: User = Depends(get_current_user)) -> WachtlProbeOut:
    p = db.get(WachtlProbe, probe_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
    return _probe_out(p, _site_names(db))


@router.patch("/probes/{probe_id}", response_model=WachtlProbeOut)
def update_probe(probe_id: int, payload: WachtlProbeUpdate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> WachtlProbeOut:
    p = db.get(WachtlProbe, probe_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
    if payload.status == "active" and p.deregistered_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Probe wurde deinstalliert – zum Reaktivieren neu installieren")
    if payload.status is not None:
        p.status = payload.status
        audit.record(db, "wachtl_probe_enable" if payload.status == "active" else "wachtl_probe_disable",
                     user_id=user.id, username=user.username, detail=f"{p.id} {p.hostname}",
                     ip=client_ip(request))
    if payload.peer_enabled is not None:
        p.peer_enabled = payload.peer_enabled
        audit.record(db, "wachtl_probe_peer", user_id=user.id, username=user.username,
                     detail=f"{p.id} {p.hostname} Gegenstelle={payload.peer_enabled}", ip=client_ip(request))
    db.commit()
    db.refresh(p)
    return _probe_out(p, _site_names(db))


@router.delete("/probes/{probe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_probe(probe_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> None:
    p = db.get(WachtlProbe, probe_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
    db.delete(p)
    audit.record(db, "wachtl_probe_delete", user_id=user.id, username=user.username,
                 detail=f"{p.id} {p.hostname}", ip=client_ip(request))
    db.commit()


@router.get("/probes/{probe_id}/checks/{check_id}/series", response_model=list[WachtlSeriesPoint])
def probe_series(
    probe_id: int,
    check_id: int,
    range: str = "24h",
    start: str | None = None,
    end: str | None = None,
    points: int = 1500,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    since, until = resolve_range(range, start, end, default="24h")
    return wachtl_service.series(db, probe_id, check_id, since, until, min(max(points, 10), 5000))


# --- Störungsmeldungen ------------------------------------------------------
def _report_out(r: WachtlReport, hosts: dict[int, str], sites: dict[int, str]) -> WachtlReportOut:
    return WachtlReportOut(
        id=r.id, probe_id=r.probe_id, site_id=r.site_id, probe_hostname=hosts.get(r.probe_id, ""),
        site_name=sites.get(r.site_id, ""), occurred_at=r.occurred_at, received_at=r.received_at,
        category=r.category, category_label=wachtl_events.REPORT_CATEGORIES.get(r.category, r.category),
        duration=r.duration, duration_label=wachtl_events.REPORT_DURATIONS.get(r.duration, r.duration),
        description=r.description, reported_user=r.reported_user, hostname=r.hostname, status=r.status,
        note=r.note, done_by=r.done_by, done_at=r.done_at, diagnostic_run_id=r.diagnostic_run_id,
    )


def _probe_hosts(db: Session) -> dict[int, str]:
    return {p.id: p.hostname for p in db.execute(select(WachtlProbe)).scalars()}


@router.get("/reports", response_model=list[WachtlReportOut])
def list_reports(
    status_: str = Query("all", alias="status", pattern="^(open|done|all)$"),
    range: str = "30d",
    start: str | None = None,
    end: str | None = None,
    site_id: int | None = None,
    probe_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WachtlReportOut]:
    since, until = resolve_range(range, start, end, default="30d")
    q = select(WachtlReport)
    if status_ != "all":
        q = q.where(WachtlReport.status == status_)
    if since is not None:
        q = q.where(WachtlReport.occurred_at >= since)
    if until is not None:
        q = q.where(WachtlReport.occurred_at <= until)
    if site_id is not None:
        q = q.where(WachtlReport.site_id == site_id)
    if probe_id is not None:
        q = q.where(WachtlReport.probe_id == probe_id)
    rows = db.execute(q.order_by(WachtlReport.occurred_at.desc()).limit(1000)).scalars()
    hosts, sites = _probe_hosts(db), _site_names(db)
    return [_report_out(r, hosts, sites) for r in rows]


@router.post("/reports/{report_id}/status", response_model=WachtlReportOut)
def set_report_status(report_id: int, payload: WachtlReportStatusUpdate, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(require_admin)) -> WachtlReportOut:
    r = db.get(WachtlReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meldung nicht gefunden")
    r.status = payload.status
    if payload.note is not None:
        r.note = payload.note.strip() or None
    r.done_by = user.username if payload.status == "done" else None
    r.done_at = utcnow() if payload.status == "done" else None
    audit.record(db, "wachtl_report_done" if payload.status == "done" else "wachtl_report_reopen",
                 user_id=user.id, username=user.username, detail=str(report_id), ip=client_ip(request))
    db.commit()
    return _report_out(r, _probe_hosts(db), _site_names(db))


# --- Client-Hinweise --------------------------------------------------------
@router.get("/findings", response_model=list[WachtlFindingOut])
def list_findings(
    days: int = Query(7, ge=1, le=400),
    probe_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WachtlFindingOut]:
    """Aktive Hinweise immer, behobene der letzten ``days`` Tage; aktive und schwere zuerst."""
    cutoff = utcnow() - timedelta(days=days)
    q = select(WachtlFinding).where(
        or_(WachtlFinding.state == "active", WachtlFinding.resolved_at >= cutoff))
    if probe_id is not None:
        q = q.where(WachtlFinding.probe_id == probe_id)
    rows = list(db.execute(q.order_by(WachtlFinding.detected_at.desc()).limit(500)).scalars())
    sev = {"critical": 0, "warning": 1, "info": 2}
    rows.sort(key=lambda f: (f.state != "active", sev.get(f.severity, 3)))
    hosts = _probe_hosts(db)
    out = []
    for f in rows:
        try:
            details = {str(k): str(v) for k, v in json.loads(f.details_json or "{}").items()}
        except (json.JSONDecodeError, AttributeError):
            details = {}
        out.append(WachtlFindingOut(
            id=f.id, probe_id=f.probe_id, probe_hostname=hosts.get(f.probe_id, ""), site_id=f.site_id,
            kind=f.kind, kind_label=wachtl_events.FINDING_KINDS.get(f.kind, f.kind), severity=f.severity,
            state=f.state, adapter=f.adapter, adapter_description=f.adapter_description, title=f.title,
            message=f.message, solution=f.solution, details=details, detected_at=f.detected_at,
            resolved_at=f.resolved_at,
        ))
    return out


# --- Ausfälle ---------------------------------------------------------------
@router.get("/incidents", response_model=list[WachtlIncidentOut])
def list_incidents(
    range: str = "24h",
    start: str | None = None,
    end: str | None = None,
    site_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WachtlIncidentOut]:
    """Störungen mit Ursache je Standort (Minutenauflösung, höchstens ``wachtl_incident_max_days``)."""
    now = utcnow()
    since, until = resolve_range(range, start, end, default="24h")
    until = min(until or now, now)
    floor = until - timedelta(days=settings.wachtl_incident_max_days)
    since = max(since or floor, floor)
    hosts = _probe_hosts(db)
    check_names = {c.id: c.name for c in db.execute(select(WachtlCheck)).scalars()}
    out = []
    for inc in wachtl_service.incidents(db, since, until, site_id):
        end_dt = datetime.fromtimestamp(inc["end"], UTC)
        out.append(WachtlIncidentOut(
            **{k: inc[k] for k in ("scope", "cause", "cause_label", "hint", "site_id", "site_name",
                                   "probe_ids", "check_id", "check_ids", "minutes", "affected_minutes",
                                   "max_loss_pct")},
            probe_hostnames=[hosts.get(p, f"#{p}") for p in inc["probe_ids"]],
            check_names=[check_names.get(c, f"#{c}") for c in inc["check_ids"]],
            start=datetime.fromtimestamp(inc["start"], UTC), end=end_dt,
            ongoing=(now - end_dt).total_seconds() < 180,
        ))
    return out


# --- Diagnose ---------------------------------------------------------------
def _start_diag(probe_id: int, report_id: int | None) -> WachtlDiagnoseStarted:
    with session_scope() as db:
        probe = db.get(WachtlProbe, probe_id)
        if probe is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe nicht gefunden")
        targets = [t["host"] for t in wachtl_service.diagnose_targets(db, probe)]
    try:
        wachtl_service.start_diagnostics(probe_id, report_id=report_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return WachtlDiagnoseStarted(started=True, targets=targets)


@router.post("/probes/{probe_id}/diagnose", response_model=WachtlDiagnoseStarted,
             status_code=status.HTTP_202_ACCEPTED)
def diagnose_probe(probe_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_admin)) -> WachtlDiagnoseStarted:
    """netprobe-Diagnose (Ping, MTU, Traceroute) zu den Zielen des Standorts und zum PC – läuft im
    Hintergrund, Ergebnis unter Netzwerk-Diagnose."""
    out = _start_diag(probe_id, None)
    audit.record(db, "wachtl_diagnose", user_id=user.id, username=user.username, detail=f"Probe {probe_id}",
                 ip=client_ip(request))
    db.commit()
    return out


@router.post("/reports/{report_id}/diagnose", response_model=WachtlDiagnoseStarted,
             status_code=status.HTTP_202_ACCEPTED)
def diagnose_report(report_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_admin)) -> WachtlDiagnoseStarted:
    r = db.get(WachtlReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meldung nicht gefunden")
    out = _start_diag(r.probe_id, r.id)
    audit.record(db, "wachtl_diagnose", user_id=user.id, username=user.username,
                 detail=f"Meldung {report_id}", ip=client_ip(request))
    db.commit()
    return out


# --- Pfad, Bandbreite, Sprache ---------------------------------------------
def _probe_check(db: Session, probe_id: int, check_id: int,
                 types: tuple[str, ...]) -> tuple[WachtlProbe, WachtlCheck]:
    probe = db.get(WachtlProbe, probe_id)
    check = db.get(WachtlCheck, check_id)
    if probe is None or check is None or check.type not in types:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Probe oder Check nicht gefunden")
    return probe, check


def _path_out(r: WachtlPathRun) -> WachtlPathRunOut:
    return WachtlPathRunOut(id=r.id, first_ts=r.first_ts, ts=r.ts, reason=r.reason, reached=r.reached,
                            changed=r.changed, hops=json.loads(r.hops_json))


@router.get("/probes/{probe_id}/checks/{check_id}/path", response_model=WachtlPathView)
def probe_path(probe_id: int, check_id: int, range: str = "1h", start: str | None = None,
               end: str | None = None, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)) -> WachtlPathView:
    """Aktueller Pfad, Pfadwechsel und MTR-Tabelle (Hop-Pings im Zeitraum, bewertet)."""
    _probe_check(db, probe_id, check_id, ("path",))
    since, until = resolve_range(range, start, end, default="1h")
    until = until or utcnow()
    runs = wachtl_measure.path_runs(db, probe_id, check_id)
    return WachtlPathView(
        current=_path_out(runs[0]) if runs else None,
        history=[_path_out(r) for r in runs if r.changed or r is runs[-1]],
        hops=wachtl_measure.hop_table(db, probe_id, check_id, since or until - timedelta(hours=1), until),
    )


@router.get("/probes/{probe_id}/checks/{check_id}/bandwidth", response_model=list[WachtlBandwidthRunOut])
def probe_bandwidth(probe_id: int, check_id: int, range: str = "30d", start: str | None = None,
                    end: str | None = None, db: Session = Depends(get_db),
                    _: User = Depends(get_current_user)) -> list:
    _probe_check(db, probe_id, check_id, ct.BANDWIDTH_TYPES)
    since, until = resolve_range(range, start, end, default="30d")
    return wachtl_measure.bandwidth_runs(db, probe_id, check_id, since, until)


@router.get("/probes/{probe_id}/checks/{check_id}/rtp", response_model=list[WachtlRtpRunOut])
def probe_rtp(probe_id: int, check_id: int, range: str = "30d", start: str | None = None,
              end: str | None = None, db: Session = Depends(get_db),
              _: User = Depends(get_current_user)) -> list:
    _probe_check(db, probe_id, check_id, ("rtp",))
    since, until = resolve_range(range, start, end, default="30d")
    return wachtl_measure.rtp_runs(db, probe_id, check_id, since, until)


@router.post("/probes/{probe_id}/checks/{check_id}/run", response_model=WachtlCommandOut,
             status_code=status.HTTP_202_ACCEPTED)
def run_check_now(probe_id: int, check_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_admin)) -> WachtlCommandOut:
    """„Jetzt messen“ (Bandbreite/Sprache) bzw. „Pfad neu ermitteln“ – geht mit dem nächsten Heartbeat
    (höchstens ``wachtl_heartbeat_interval_s``) an die Probe."""
    probe, check = _probe_check(db, probe_id, check_id, ("path", *ct.SCHEDULED_TYPES))
    if probe.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Probe ist deaktiviert")
    ctype = "retrace" if check.type == "path" else "run_check"
    cmd = wachtl_measure.queue_command(db, probe.id, ctype, check.id, user.username)
    audit.record(db, "wachtl_command", user_id=user.id, username=user.username,
                 detail=f"{ctype} Probe {probe.id} Check {check.id}", ip=client_ip(request))
    db.commit()
    return WachtlCommandOut(id=cmd.id, type=cmd.type, check_id=cmd.check_id)


# --- Ereignisse -------------------------------------------------------------
@router.get("/events", response_model=list[WachtlEventOut])
def list_events(range: str = "7d", start: str | None = None, end: str | None = None,
                site_id: int | None = None, probe_id: int | None = None, db: Session = Depends(get_db),
                _: User = Depends(get_current_user)) -> list[WachtlEventOut]:
    """Einzelereignisse (Pfadwechsel, Bandbreite/MOS unter Schwellwert, Messfehler)."""
    since, until = resolve_range(range, start, end, default="7d")
    q = select(WachtlEvent)
    if since is not None:
        q = q.where(WachtlEvent.ts >= since)
    if until is not None:
        q = q.where(WachtlEvent.ts <= until)
    if site_id is not None:
        q = q.where(WachtlEvent.site_id == site_id)
    if probe_id is not None:
        q = q.where(WachtlEvent.probe_id == probe_id)
    hosts, sites = _probe_hosts(db), _site_names(db)
    return [
        WachtlEventOut(id=e.id, ts=e.ts, site_id=e.site_id, site_name=sites.get(e.site_id, ""),
                       probe_id=e.probe_id, probe_hostname=hosts.get(e.probe_id) if e.probe_id else None,
                       check_id=e.check_id, kind=e.kind, severity=e.severity, title=e.title, detail=e.detail,
                       notified_at=e.notified_at)
        for e in db.execute(q.order_by(WachtlEvent.ts.desc()).limit(1000)).scalars()
    ]
