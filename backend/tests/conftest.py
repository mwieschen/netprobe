"""Pytest-Fixtures. Konfiguriert netprobe auf eine temporäre SQLite-DB, bevor
die App-Module importiert werden."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Konfiguration MUSS vor dem Import der App gesetzt werden.
_TMP = Path(tempfile.mkdtemp(prefix="netprobe-test-"))
os.environ.setdefault("NETPROBE_DATABASE_URL", f"sqlite:///{(_TMP / 'test.sqlite').as_posix()}")
os.environ.setdefault("NETPROBE_DATA_DIR", str(_TMP))
os.environ.setdefault("NETPROBE_CAPTURE_DIR", str(_TMP / "captures"))
os.environ.setdefault("NETPROBE_LOG_DIR", str(_TMP / "logs"))
os.environ.setdefault("NETPROBE_FRONTEND_DIST", str(_TMP / "dist"))
os.environ.setdefault("NETPROBE_SECRET_KEY", "test-secret-key")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    import app.models  # noqa: F401
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    """Legt einen Admin an und gibt einen authentifizierten Client zurück."""
    from app.core.security import hash_password
    from app.db.session import session_scope
    from app.models.user import User

    with session_scope() as db:
        if not db.query(User).filter(User.username == "admin").first():
            db.add(User(username="admin", password_hash=hash_password("password123"), role="admin"))

    resp = client.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    assert resp.status_code == 200, resp.text
    return client
