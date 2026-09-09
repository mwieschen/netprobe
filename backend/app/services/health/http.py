"""HTTP(S)-Erreichbarkeitstest.

Parameter (``params``):
- ``method``: ``GET`` (Standard) oder ``HEAD``.
- ``expect_status``: erwarteter Statuscode (Standard: 2x/3xx gelten als ok).
- ``expect_substring``: erwarteter Text im Body, optional.
- ``verify_tls``: Zertifikat prüfen (Standard ``true``).
- ``cert_warn_days``: Warnschwelle Zertifikatslaufzeit (Standard 21).
"""

from __future__ import annotations

import socket
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from app.services.health import HealthOutcome, register

_TIMEOUT = 6.0
_CERT_FMT = "%b %d %H:%M:%S %Y %Z"


def _normalize(target: str) -> str:
    if not target.startswith(("http://", "https://")):
        return "https://" + target
    return target


def _cert_days_left(host: str, port: int) -> tuple[int | None, str | None]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=_TIMEOUT) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
        not_after_raw = cert.get("notAfter") if isinstance(cert, dict) else None
        if not not_after_raw:
            return None, None
        not_after = datetime.strptime(str(not_after_raw), _CERT_FMT).replace(tzinfo=UTC)
        return (not_after - datetime.now(UTC)).days, not_after.isoformat()
    except (OSError, ssl.SSLError, ValueError, KeyError):
        return None, None


def run(target: str, params: dict) -> HealthOutcome:
    url = _normalize(target)
    parsed = urlparse(url)
    method = str(params.get("method", "GET")).upper()
    expect_status = params.get("expect_status")
    substring = str(params.get("expect_substring", "")).strip()
    verify_tls = bool(params.get("verify_tls", True))
    cert_warn_days = int(params.get("cert_warn_days", 21))

    details: dict = {"url": url}
    errors: list[str] = []

    start = time.perf_counter()
    try:
        resp = httpx.request(
            method, url, timeout=_TIMEOUT, follow_redirects=True, verify=verify_tls,
            headers={"User-Agent": "netprobe-healthcheck"},
        )
    except httpx.HTTPError as exc:
        return HealthOutcome(ok=False, error=f"HTTP-Anfrage fehlgeschlagen: {exc}",
                             details=details, status="unerreichbar")
    elapsed = round((time.perf_counter() - start) * 1000, 2)

    body = "" if method == "HEAD" else resp.text[:65536]
    details["status_code"] = resp.status_code
    details["final_url"] = str(resp.url)

    status_ok = (
        resp.status_code == expect_status if expect_status is not None
        else 200 <= resp.status_code < 400
    )
    if not status_ok:
        errors.append(f"Status {resp.status_code} (erwartet {expect_status or '2xx/3xx'})")

    substr_ok = (not substring) or (substring in body)
    if not substr_ok:
        errors.append(f"Text {substring!r} nicht im Body")

    if parsed.scheme == "https":
        days, not_after = _cert_days_left(parsed.hostname or "", parsed.port or 443)
        if days is not None:
            details["cert_days_left"] = days
            details["cert_not_after"] = not_after
            if days < 0:
                errors.append("TLS-Zertifikat ist abgelaufen")
            elif days < cert_warn_days:
                errors.append(f"TLS-Zertifikat läuft in {days} Tagen ab")

    ok = status_ok and substr_ok and not any("abgelaufen" in e for e in errors)
    return HealthOutcome(
        ok=ok,
        response_ms=elapsed,
        status=f"{resp.status_code} {resp.reason_phrase}".strip(),
        details=details,
        error="; ".join(errors) if errors else None,
    )


register("http", run)
