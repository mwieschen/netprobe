"""Gemeinsame FastAPI-Dependencies: aktueller Benutzer, RBAC."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import SESSION_COOKIE, unsign_session
from app.db.session import get_db
from app.models.user import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    user_id = unsign_session(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sitzung ungültig oder abgelaufen")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer nicht verfügbar")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adminrechte erforderlich")
    return user


def client_ip(request: Request) -> str:
    # Nur der direkte Peer; hinter nginx via X-Forwarded-For (vertrauenswürdig im LAN)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
