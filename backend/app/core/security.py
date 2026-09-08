"""Sicherheits-Primitive: Passwort-Hashing (Argon2), signierte Session-Cookies,
Login-Rate-Limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.core.config import settings

_ph = PasswordHasher()
_signer = TimestampSigner(settings.secret_key, salt="netprobe-session")

SESSION_COOKIE = "netprobe_session"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


def sign_session(user_id: int) -> str:
    return _signer.sign(str(user_id).encode()).decode()


def unsign_session(token: str, max_age: int | None = None) -> int | None:
    try:
        raw = _signer.unsign(
            token, max_age=max_age if max_age is not None else settings.session_max_age_s
        )
        return int(raw.decode())
    except (BadSignature, SignatureExpired, ValueError):
        return None


class RateLimiter:
    """Sliding-Window-Rate-Limiter im Speicher (pro Prozess). Für eine
    Single-Node-Appliance ausreichend."""

    def __init__(self, max_attempts: int, window_s: int) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        """True, wenn ein weiterer Versuch erlaubt ist."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return len(q) < self.max_attempts

    def hit(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


login_rate_limiter = RateLimiter(
    settings.login_rate_max_attempts, settings.login_rate_window_s
)
