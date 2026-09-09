"""CLI-Unterbefehle für Datenbank-Setup und Admin-Benutzerverwaltung."""

from __future__ import annotations

import argparse
import getpass
import sys

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import engine, session_scope
from app.models.user import User


def init_db() -> None:
    """Erstellt alle Tabellen (idempotent). Für Produktivbetrieb bevorzugt Alembic."""
    import app.models  # noqa: F401  (Registrierung der Modelle)

    Base.metadata.create_all(bind=engine)
    print("Datenbank-Schema erstellt/aktualisiert.")


def _create_admin(username: str, password: str, force: bool = False) -> bool:
    with session_scope() as db:
        existing = db.query(User).filter(User.username == username).first()
        if existing and not force:
            print(f"Benutzer '{username}' existiert bereits – übersprungen.")
            return False
        if existing:
            existing.password_hash = hash_password(password)
            existing.role = "admin"
            existing.is_active = True
            print(f"Admin '{username}' aktualisiert.")
        else:
            db.add(User(username=username, password_hash=hash_password(password), role="admin"))
            print(f"Admin '{username}' angelegt.")
        return True


def create_admin(args: argparse.Namespace) -> None:
    username = args.username or input("Admin-Benutzername: ").strip()
    password = args.password
    if not password:
        password = getpass.getpass("Passwort: ")
        confirm = getpass.getpass("Passwort bestätigen: ")
        if password != confirm:
            print("Passwörter stimmen nicht überein.", file=sys.stderr)
            sys.exit(1)
    if len(password) < 8:
        print("Passwort muss mindestens 8 Zeichen haben.", file=sys.stderr)
        sys.exit(1)
    _create_admin(username, password, force=args.force)


def ensure_admin(_: argparse.Namespace) -> None:
    """Legt beim allerersten Start einen Admin aus Env-Variablen an, sofern noch
    kein Benutzer existiert. Passwort wird niemals geloggt."""
    with session_scope() as db:
        if db.query(User).count() > 0:
            print("Es existieren bereits Benutzer – ensure-admin übersprungen.")
            return
    if not settings.admin_user or not settings.admin_password:
        print("NETPROBE_ADMIN_USER/NETPROBE_ADMIN_PASSWORD nicht gesetzt – kein Admin angelegt.")
        return
    _create_admin(settings.admin_user, settings.admin_password)
