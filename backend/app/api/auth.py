"""Authentifizierungs-Endpunkte."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.config import settings
from app.core.security import (
    SESSION_COOKIE,
    hash_password,
    login_rate_limiter,
    needs_rehash,
    sign_session,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.schemas import LoginRequest, UserOut
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user_id),
        max_age=settings.session_max_age_s,
        httponly=True,
        secure=settings.https_enabled,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    ip = client_ip(request)
    rate_key = f"{ip}:{payload.username}"
    if not login_rate_limiter.check(rate_key):
        audit.record(db, "login_rate_limited", username=payload.username, ip=ip)
        db.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Anmeldeversuche. Bitte später erneut versuchen.",
        )

    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not user.is_active or not verify_password(user.password_hash, payload.password):
        login_rate_limiter.hit(rate_key)
        audit.record(db, "login_failed", username=payload.username, ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldedaten ungültig")

    # Passwort-Hash bei Bedarf modernisieren
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    login_rate_limiter.reset(rate_key)
    _set_session_cookie(response, user.id)
    audit.record(db, "login_success", user_id=user.id, username=user.username, ip=ip)
    db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
