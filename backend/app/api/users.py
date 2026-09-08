"""Benutzerverwaltung (nur admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.core.security import hash_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.schemas import UserCreate, UserOut, UserUpdate
from app.services import audit

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[User]:
    return list(db.execute(select(User).order_by(User.id)).scalars().all())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Benutzername existiert bereits")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    audit.record(db, "user_create", user_id=admin.id, username=admin.username,
                 detail=payload.username, ip=client_ip(request))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data.pop("password"))
    for k, v in data.items():
        if v is not None:
            setattr(user, k, v)
    audit.record(db, "user_update", user_id=admin.id, username=admin.username, detail=str(user_id))
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenes Konto kann nicht gelöscht werden")
    if user.role == "admin":
        remaining = db.query(User).filter(User.role == "admin", User.id != user_id).count()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Letzter Admin kann nicht gelöscht werden")
    db.delete(user)
    audit.record(db, "user_delete", user_id=admin.id, username=admin.username, detail=str(user_id))
    db.commit()
