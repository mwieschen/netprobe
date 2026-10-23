"""E-Mail-Benachrichtigung der Wachtl-Clients (``/api/clients/notify``): SMTP-Einstellungen, Empfänger,
Testmail und Versandprotokoll. Alles nur für Admins (enthält SMTP-Zugangsdaten und Adressen)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import client_ip, require_admin
from app.db.session import get_db, session_scope
from app.models.user import User
from app.models.wachtl import WachtlNotification, WachtlRecipient, WachtlSite
from app.schemas.schemas import (
    WachtlNotificationOut,
    WachtlNotifySettingsOut,
    WachtlNotifySettingsUpdate,
    WachtlRecipientCreate,
    WachtlRecipientOut,
    WachtlRecipientUpdate,
    WachtlTestMailRequest,
)
from app.services import audit, wachtl_notify
from app.services import wachtl_notify_compose as nc
from app.services.wachtl_checktypes import valid_host

router = APIRouter(prefix="/api/clients/notify", tags=["clients"])


def _settings_out(cfg: dict) -> WachtlNotifySettingsOut:
    return WachtlNotifySettingsOut(
        **wachtl_notify.public_config(cfg), ready_error=wachtl_notify.smtp_ready(cfg)
    )


@router.get("/settings", response_model=WachtlNotifySettingsOut)
def get_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> WachtlNotifySettingsOut:
    return _settings_out(wachtl_notify.get_config(db))


@router.put("/settings", response_model=WachtlNotifySettingsOut)
def update_settings(
    payload: WachtlNotifySettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WachtlNotifySettingsOut:
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("smtp_host"):
        changes["smtp_host"] = changes["smtp_host"].strip()
        if not valid_host(changes["smtp_host"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger SMTP-Server")
    if changes.get("smtp_from"):
        changes["smtp_from"] = changes["smtp_from"].strip()
        if not nc.valid_email(changes["smtp_from"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige Absenderadresse")
    if changes.get("public_url"):
        changes["public_url"] = changes["public_url"].strip().rstrip("/")
        if not changes["public_url"].startswith(("http://", "https://")):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Adresse von netprobe muss mit http(s):// beginnen"
            )
    cfg = wachtl_notify.update_config(db, dict(changes))
    # Nie das Passwort ins Audit-Log – nur, dass es geändert wurde
    fields = sorted(k for k in changes if k != "smtp_password")
    if "smtp_password" in changes:
        fields.append("smtp_password(geändert)")
    audit.record(
        db,
        "wachtl_notify_settings",
        user_id=user.id,
        username=user.username,
        detail=", ".join(fields),
        ip=client_ip(request),
    )
    db.commit()
    return _settings_out(cfg)


@router.post("/test", status_code=status.HTTP_202_ACCEPTED)
async def send_test(
    payload: WachtlTestMailRequest, request: Request, user: User = Depends(require_admin)
) -> dict:
    """Testmail synchron senden (Fehlermeldung des SMTP-Servers geht an die Oberfläche)."""
    to = payload.to.strip()
    if not nc.valid_email(to):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige Empfängeradresse")

    def _send() -> str | None:
        with session_scope() as db:
            try:
                wachtl_notify.test_mail(db, to)
                error = None
            except wachtl_notify.NotifyError as exc:
                error = str(exc)
            audit.record(
                db,
                "wachtl_notify_test",
                user_id=user.id,
                username=user.username,
                detail=f"{to}: {'ok' if error is None else error}"[:500],
                ip=client_ip(request),
            )
            return error

    error = await run_in_threadpool(_send)
    if error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, error)
    return {"sent": True, "to": to}


@router.get("/log", response_model=list[WachtlNotificationOut])
def notification_log(
    limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> list[WachtlNotification]:
    return list(
        db.execute(
            select(WachtlNotification)
            .order_by(WachtlNotification.ts.desc(), WachtlNotification.id.desc())
            .limit(min(max(limit, 1), 500))
        ).scalars()
    )


# --- Empfänger --------------------------------------------------------------
def _check_recipient(db: Session, data: dict) -> None:
    if "email" in data and data["email"] is not None:
        data["email"] = data["email"].strip()
        if not nc.valid_email(data["email"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige E-Mail-Adresse")
    if data.get("site_id") is not None and db.get(WachtlSite, data["site_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht gefunden")


@router.get("/recipients", response_model=list[WachtlRecipientOut])
def list_recipients(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[WachtlRecipient]:
    return list(db.execute(select(WachtlRecipient).order_by(WachtlRecipient.name)).scalars())


@router.post("/recipients", response_model=WachtlRecipientOut, status_code=status.HTTP_201_CREATED)
def create_recipient(
    payload: WachtlRecipientCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WachtlRecipient:
    data = payload.model_dump()
    _check_recipient(db, data)
    r = WachtlRecipient(**data)
    db.add(r)
    audit.record(
        db,
        "wachtl_recipient_create",
        user_id=user.id,
        username=user.username,
        detail=f"{r.name} <{r.email}>",
        ip=client_ip(request),
    )
    db.commit()
    db.refresh(r)
    return r


@router.patch("/recipients/{recipient_id}", response_model=WachtlRecipientOut)
def update_recipient(
    recipient_id: int,
    payload: WachtlRecipientUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> WachtlRecipient:
    r = db.get(WachtlRecipient, recipient_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empfänger nicht gefunden")
    data = payload.model_dump(exclude_unset=True)
    _check_recipient(db, data)
    for k, v in data.items():
        if v is not None or k == "site_id":
            setattr(r, k, v)
    audit.record(
        db,
        "wachtl_recipient_update",
        user_id=user.id,
        username=user.username,
        detail=f"{r.id} <{r.email}>",
        ip=client_ip(request),
    )
    db.commit()
    db.refresh(r)
    return r


@router.delete("/recipients/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipient(
    recipient_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
) -> None:
    r = db.get(WachtlRecipient, recipient_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empfänger nicht gefunden")
    db.delete(r)
    audit.record(
        db,
        "wachtl_recipient_delete",
        user_id=user.id,
        username=user.username,
        detail=f"{r.id} <{r.email}>",
        ip=client_ip(request),
    )
    db.commit()
