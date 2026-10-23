"""Verwaltung der Wachtl-Clients (``/api/clients``): Standorte, Enrollment-Token, Checks, Probes,
Standort-Matrix und Zeitreihen. Die Probes selbst sprechen ``/api/probe/v1`` (``app.api.wachtl_probe``)."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.api.timerange import resolve_range
from app.core.config import settings
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models.user import User
from app.models.wachtl import WachtlCheck, WachtlEnrollToken, WachtlProbe, WachtlSite
from app.schemas.schemas import (
    WachtlCheckCreate,
    WachtlCheckOut,
    WachtlCheckUpdate,
    WachtlOverview,
    WachtlProbeOut,
    WachtlProbeUpdate,
    WachtlSeriesPoint,
    WachtlSettingsOut,
    WachtlSettingsUpdate,
    WachtlSiteCreate,
    WachtlSiteOut,
    WachtlTokenCreate,
    WachtlTokenCreated,
    WachtlTokenOut,
)
from app.services import audit, kvstore, wachtl_service
from app.services.proc import validate_network_target

router = APIRouter(prefix="/api/clients", tags=["clients"])


# --- Einstellungen ----------------------------------------------------------
def _settings_out(db: Session) -> WachtlSettingsOut:
    pin, pin_error = wachtl_service.tls_pin()
    return WachtlSettingsOut(
        enabled=wachtl_service.is_enabled(db),
        pin=pin,
        pin_error=pin_error,
        server_urls=settings.wachtl_server_url_list,
        min_interval_s=settings.wachtl_min_interval_s,
    )


@router.get("/settings", response_model=WachtlSettingsOut)
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> WachtlSettingsOut:
    return _settings_out(db)


@router.put("/settings", response_model=WachtlSettingsOut)
def update_settings(payload: WachtlSettingsUpdate, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_admin)) -> WachtlSettingsOut:
    kvstore.set_value(db, wachtl_service.KV_ENABLED, payload.enabled)
    audit.record(db, "wachtl_enable" if payload.enabled else "wachtl_disable", user_id=user.id,
                 username=user.username, ip=client_ip(request))
    db.commit()
    return _settings_out(db)


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
def _validate_check(db: Session, target: str | None, interval_s: int | None, site_id: int | None) -> None:
    if target is not None and target != "gateway":
        try:
            validate_network_target(target)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Ungültiges Ziel – IP-Adresse, Hostname oder 'gateway'") from exc
    if interval_s is not None and interval_s < settings.wachtl_min_interval_s:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Intervall mindestens {settings.wachtl_min_interval_s} s")
    if site_id is not None and db.get(WachtlSite, site_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht gefunden")


@router.get("/checks", response_model=list[WachtlCheckOut])
def list_checks(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[WachtlCheck]:
    return list(db.execute(select(WachtlCheck).order_by(WachtlCheck.id)).scalars())


@router.post("/checks", response_model=WachtlCheckOut, status_code=status.HTTP_201_CREATED)
def create_check(payload: WachtlCheckCreate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> WachtlCheck:
    data = payload.model_dump()
    data["target"] = data["target"].strip()
    _validate_check(db, data["target"], data["interval_s"], data["site_id"])
    check = WachtlCheck(type="ping", params_json=json.dumps({}), **data)
    db.add(check)
    audit.record(db, "wachtl_check_create", user_id=user.id, username=user.username,
                 detail=f"{check.name} → {check.target}", ip=client_ip(request))
    db.commit()
    db.refresh(check)
    return check


@router.patch("/checks/{check_id}", response_model=WachtlCheckOut)
def update_check(check_id: int, payload: WachtlCheckUpdate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)) -> WachtlCheck:
    check = db.get(WachtlCheck, check_id)
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check nicht gefunden")
    changes = payload.model_dump(exclude_unset=True)
    if "target" in changes and changes["target"] is not None:
        changes["target"] = changes["target"].strip()
    _validate_check(db, changes.get("target"), changes.get("interval_s"), changes.get("site_id"))
    for k, v in changes.items():
        if v is None and k != "site_id":
            continue
        setattr(check, k, v)
    audit.record(db, "wachtl_check_update", user_id=user.id, username=user.username, detail=str(check_id),
                 ip=client_ip(request))
    db.commit()
    db.refresh(check)
    return check


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
        addresses=addresses, deregistered_at=p.deregistered_at,
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
    p.status = payload.status
    audit.record(db, "wachtl_probe_enable" if payload.status == "active" else "wachtl_probe_disable",
                 user_id=user.id, username=user.username, detail=f"{p.id} {p.hostname}",
                 ip=client_ip(request))
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
