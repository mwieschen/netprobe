"""SMTP-/IMAP-Verbindungstest (nur Handshake, **kein Login**).

Parameter (``params``):
- ``port``: Zielport. Standard SMTP 25, IMAP 143. Implizites TLS: SMTP 465 / IMAP 993.
- ``starttls``: bei ``true`` wird STARTTLS auch ausgehandelt (Standard: nur Fähigkeit prüfen).
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from datetime import UTC, datetime

from app.services.health import HealthOutcome, register

_TIMEOUT = 8.0
_CERT_FMT = "%b %d %H:%M:%S %Y %Z"


def _cert_expiry(sock: ssl.SSLSocket) -> str | None:
    try:
        cert = sock.getpeercert()
        raw = cert.get("notAfter") if isinstance(cert, dict) else None
        if not raw:
            return None
        return datetime.strptime(str(raw), _CERT_FMT).replace(tzinfo=UTC).isoformat()
    except (ValueError, KeyError, AttributeError):
        return None


def _run_smtp(host: str, port: int, starttls: bool) -> HealthOutcome:
    details: dict = {"protocol": "smtp", "port": port}
    start = time.perf_counter()
    implicit_tls = port == 465
    try:
        if implicit_tls:
            client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT)
        else:
            client = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        code, msg = client.ehlo("netprobe.local")
        details["ehlo_code"] = code
        can_starttls = client.has_extn("starttls")
        details["starttls_available"] = can_starttls
        did_starttls = False
        if starttls and can_starttls and not implicit_tls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo("netprobe.local")
            did_starttls = True
        if isinstance(client.sock, ssl.SSLSocket):
            details["cert_not_after"] = _cert_expiry(client.sock)
        client.quit()
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return HealthOutcome(ok=False, status="unerreichbar",
                             error=f"SMTP fehlgeschlagen: {exc}", details=details)

    elapsed = round((time.perf_counter() - start) * 1000, 2)
    tls_txt = "implizites TLS" if implicit_tls else ("STARTTLS" if did_starttls else
              ("STARTTLS verfügbar" if can_starttls else "kein STARTTLS"))
    ok = bool(200 <= details["ehlo_code"] < 400)
    if starttls and not implicit_tls and not can_starttls:
        ok = False
    return HealthOutcome(ok=ok, response_ms=elapsed, status=f"EHLO {details['ehlo_code']}, {tls_txt}",
                         details=details)


def _run_imap(host: str, port: int, starttls: bool) -> HealthOutcome:
    details: dict = {"protocol": "imap", "port": port}
    start = time.perf_counter()
    implicit_tls = port == 993
    try:
        if implicit_tls:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=_TIMEOUT)
        else:
            client = imaplib.IMAP4(host, port, timeout=_TIMEOUT)
        caps = list(client.capabilities)
        details["capabilities"] = caps
        can_starttls = "STARTTLS" in caps
        details["starttls_available"] = can_starttls
        did_starttls = False
        if starttls and can_starttls and not implicit_tls:
            client.starttls(ssl.create_default_context())
            did_starttls = True
        sock = getattr(client, "sock", None)
        if isinstance(sock, ssl.SSLSocket):
            details["cert_not_after"] = _cert_expiry(sock)
        client.logout()
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        return HealthOutcome(ok=False, status="unerreichbar",
                             error=f"IMAP fehlgeschlagen: {exc}", details=details)

    elapsed = round((time.perf_counter() - start) * 1000, 2)
    tls_txt = "implizites TLS" if implicit_tls else ("STARTTLS" if did_starttls else
              ("STARTTLS verfügbar" if can_starttls else "kein STARTTLS"))
    ok = True
    if starttls and not implicit_tls and not can_starttls:
        ok = False
    return HealthOutcome(ok=ok, response_ms=elapsed, status=f"CAPABILITY ok, {tls_txt}",
                         details=details)


def run_smtp(target: str, params: dict) -> HealthOutcome:
    host = target.split(":")[0]
    port = int(params.get("port", 25))
    return _run_smtp(host, port, bool(params.get("starttls", False)))


def run_imap(target: str, params: dict) -> HealthOutcome:
    host = target.split(":")[0]
    port = int(params.get("port", 143))
    return _run_imap(host, port, bool(params.get("starttls", False)))


register("smtp", run_smtp)
register("imap", run_imap)
