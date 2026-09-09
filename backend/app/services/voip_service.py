"""VoIP-/3CX-Verbindungstests.

Prüft die Erreichbarkeit einer 3CX-/SIP-Anlage **ohne einen echten Anruf**:

- TCP-Erreichbarkeit der SIP-, SIP-TLS- und WebRTC-/HTTPS-Ports,
- SIP-``OPTIONS``-Ping (UDP oder TCP) inkl. Antwortzeit und Statuscode,
- TLS-Handshake-Zeit und Zertifikatslaufzeit am SIP-TLS-Port,
- ICMP-Paketverlust/RTT zum Host (über :mod:`app.services.wan_service`),
- optional: ein einzelner ``REGISTER`` mit Digest-Auth (nur wenn Zugangsdaten
  übergeben werden – diese werden **nie** gespeichert oder geloggt).

Ausschließlich Standardbibliothek (``socket``/``ssl``/``hashlib``), keine
zusätzliche Abhängigkeit. Jede Teilprüfung hat ein hartes Timeout und gibt ein
strukturiertes Ergebnis zurück statt eine Ausnahme zu werfen.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import socket
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.voip import VoipServer, VoipTest
from app.services import wan_service
from app.services.proc import validate_network_target

log = get_logger("netprobe.voip")

DEFAULT_TIMEOUT = 5.0
_STATUS_RE = re.compile(rb"^SIP/2\.0\s+(\d{3})")
_CERT_DATE_FMT = "%b %d %H:%M:%S %Y %Z"


# --------------------------------------------------------------------------- #
# Port- und TLS-Prüfungen
# --------------------------------------------------------------------------- #

def check_tcp_port(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, float | None]:
    """TCP-Connect-Test. Rückgabe ``(erreichbar, connect_zeit_ms)``."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - start) * 1000, 2)
    except OSError:
        return False, None


def _parse_cert_expiry(cert: dict | None) -> datetime | None:
    if not cert or "notAfter" not in cert:
        return None
    try:
        return datetime.strptime(cert["notAfter"], _CERT_DATE_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def check_tls(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """TLS-Handshake am SIP-TLS-Port.

    Primär mit Zertifikatsprüfung (``ok``/``verified`` = True). Schlägt die
    Verifikation fehl, wird der Fehler vermerkt und – nur zur Zeitmessung –
    einmalig ein Handshake ohne Verifikation versucht. Über diesen Socket werden
    niemals Daten gesendet.
    """
    result: dict = {
        "ok": False,
        "verified": False,
        "handshake_ms": None,
        "cert_expiry": None,
        "error": None,
    }
    def _handshake(ctx: ssl.SSLContext) -> tuple[float, dict | None]:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            return round((time.perf_counter() - start) * 1000, 2), tls.getpeercert()

    try:
        ms, cert = _handshake(ssl.create_default_context())
        result.update(handshake_ms=ms, cert_expiry=_parse_cert_expiry(cert), ok=True, verified=True)
        return result
    except ssl.SSLError as exc:
        result["error"] = f"TLS-Zertifikat nicht verifizierbar: {exc.reason or exc}"
    except OSError as exc:
        result["error"] = f"TLS-Verbindung fehlgeschlagen: {exc}"
        return result

    # Zweiter Versuch nur zur Handshake-Zeitmessung (keine Verifikation, keine Daten
    # werden über diesen Socket gesendet). ``tls_verified`` bleibt False.
    insecure = ssl._create_unverified_context()  # noqa: S323 - bewusst, reine Zeitmessung
    try:
        ms, cert = _handshake(insecure)
        result.update(handshake_ms=ms, cert_expiry=_parse_cert_expiry(cert), ok=True)
    except OSError as exc:
        result["error"] = f"TLS-Verbindung fehlgeschlagen: {exc}"
    return result


# --------------------------------------------------------------------------- #
# SIP OPTIONS / REGISTER
# --------------------------------------------------------------------------- #

def _rand_token(n: int = 10) -> str:
    return secrets.token_hex(n)


def _local_addr(sock: socket.socket, fallback_host: str) -> tuple[str, int]:
    try:
        ip, port = sock.getsockname()[:2]
        if ip and ip != "0.0.0.0":  # noqa: S104 - Vergleich, kein Bind
            return ip, port
    except OSError:
        pass
    return fallback_host, 0


def _build_sip_request(
    method: str,
    host: str,
    port: int,
    transport: str,
    local_ip: str,
    local_port: int,
    *,
    call_id: str,
    from_tag: str,
    branch: str,
    cseq: int,
    user: str = "netprobe",
    extra_headers: list[str] | None = None,
) -> bytes:
    uri = f"sip:{host}"
    lines = [
        f"{method} {uri} SIP/2.0",
        f"Via: SIP/2.0/{transport.upper()} {local_ip}:{local_port};branch=z9hG4bK{branch};rport",
        "Max-Forwards: 70",
        f"From: <sip:{user}@{host}>;tag={from_tag}",
        f"To: <sip:{user}@{host}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        f"Contact: <sip:{user}@{local_ip}:{local_port};transport={transport}>",
        "User-Agent: netprobe",
        "Accept: application/sdp",
    ]
    if method == "REGISTER":
        lines.append("Expires: 60")
    lines += extra_headers or []
    lines += ["Content-Length: 0", "", ""]
    return "\r\n".join(lines).encode("utf-8")


def _parse_status(data: bytes) -> int | None:
    m = _STATUS_RE.match(data.strip())
    return int(m.group(1)) if m else None


def _send_recv(
    host: str,
    port: int,
    transport: str,
    payload_builder: Callable[[str, int], bytes],
    timeout: float,
) -> tuple[bytes, tuple[str, int]]:
    """Schickt eine per ``payload_builder(local_ip, local_port)`` erzeugte
    SIP-Nachricht und liest die erste Antwort. Rückgabe ``(antwort, local_addr)``."""
    if transport == "tcp":
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            local_ip, local_port = _local_addr(sock, host)
            sock.sendall(payload_builder(local_ip, local_port))
            return sock.recv(65535), (local_ip, local_port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        local_ip, local_port = _local_addr(sock, host)
        sock.send(payload_builder(local_ip, local_port))
        data, _ = sock.recvfrom(65535)
        return data, (local_ip, local_port)


def sip_options_ping(
    host: str, port: int, transport: str = "udp", timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """SIP-``OPTIONS``-Ping. Rückgabe ``{reachable, response_ms, status_code, error}``.

    Ein Statuscode (auch 401/403/405) bedeutet: der SIP-Stack antwortet – der
    Endpunkt ist erreichbar. Kein Empfang ⇒ ``reachable=False`` (bei UDP ambig:
    "offen oder gefiltert").
    """
    call_id = f"{_rand_token()}@netprobe"
    from_tag = _rand_token(6)
    branch = _rand_token(8)

    def build(local_ip: str, local_port: int) -> bytes:
        return _build_sip_request(
            "OPTIONS", host, port, transport, local_ip, local_port,
            call_id=call_id, from_tag=from_tag, branch=branch, cseq=1,
        )

    start = time.perf_counter()
    try:
        data, _ = _send_recv(host, port, transport, build, timeout)
    except TimeoutError:
        return {"reachable": False, "response_ms": None, "status_code": None,
                "error": "Keine SIP-Antwort (Timeout – Port offen oder gefiltert)."}
    except OSError as exc:
        return {"reachable": False, "response_ms": None, "status_code": None,
                "error": f"SIP-Verbindung fehlgeschlagen: {exc}"}

    code = _parse_status(data)
    return {
        "reachable": code is not None,
        "response_ms": round((time.perf_counter() - start) * 1000, 2),
        "status_code": code,
        "error": None if code is not None else "Antwort war keine gültige SIP-Statuszeile.",
    }


def _digest_header(params: dict, username: str, password: str, method: str, uri: str) -> str:
    realm = params.get("realm", "")
    nonce = params.get("nonce", "")
    qop = params.get("qop")
    opaque = params.get("opaque")
    algorithm = params.get("algorithm", "MD5")

    def md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()  # noqa: S324 - SIP-Digest ist per RFC MD5

    ha1 = md5(f"{username}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")
    if qop:
        qop = qop.split(",")[0].strip()
        nc = "00000001"
        cnonce = _rand_token(8)
        response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        parts = [
            f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"',
            f'uri="{uri}"', f'response="{response}"', f'algorithm={algorithm}',
            f'qop={qop}', f'nc={nc}', f'cnonce="{cnonce}"',
        ]
    else:
        response = md5(f"{ha1}:{nonce}:{ha2}")
        parts = [
            f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"',
            f'uri="{uri}"', f'response="{response}"', f'algorithm={algorithm}',
        ]
    if opaque:
        parts.append(f'opaque="{opaque}"')
    return "Digest " + ", ".join(parts)


def _parse_auth_challenge(data: bytes) -> tuple[str, dict] | None:
    text = data.decode("utf-8", errors="replace")
    for line in text.splitlines():
        low = line.lower()
        if low.startswith("www-authenticate:") or low.startswith("proxy-authenticate:"):
            header = "Proxy-Authorization" if low.startswith("proxy") else "Authorization"
            value = line.split(":", 1)[1].strip()
            params = {
                k: (v1 or v2)
                for k, v1, v2 in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', value)
            }
            return header, params
    return None


def sip_register_test(
    host: str,
    port: int,
    transport: str,
    username: str,
    password: str,
    *,
    use_tls: bool = False,
    timeout: float = 8.0,
) -> dict:
    """Ein einzelner ``REGISTER`` mit Digest-Auth. Rückgabe ``{ok, detail}``.

    Über TLS ausschließlich mit verifiziertem Zertifikat – Zugangsdaten werden
    nie über einen unverifizierten Kanal gesendet.
    """
    uri = f"sip:{host}"
    call_id = f"{_rand_token()}@netprobe"
    from_tag = _rand_token(6)

    def open_sock() -> socket.socket:
        raw = socket.create_connection((host, port), timeout=timeout)
        raw.settimeout(timeout)
        if use_tls:
            ctx = ssl.create_default_context()
            return ctx.wrap_socket(raw, server_hostname=host)
        return raw

    try:
        sock = open_sock()
    except ssl.SSLError as exc:
        return {"ok": False, "detail": f"REGISTER abgebrochen – TLS nicht verifizierbar: {exc.reason or exc}"}
    except OSError as exc:
        return {"ok": False, "detail": f"Verbindung fehlgeschlagen: {exc}"}

    try:
        local_ip, local_port = _local_addr(sock, host)
        first = _build_sip_request(
            "REGISTER", host, port, "tls" if use_tls else transport, local_ip, local_port,
            call_id=call_id, from_tag=from_tag, branch=_rand_token(8), cseq=1, user=username,
        )
        sock.sendall(first)
        data = sock.recv(65535)
        code = _parse_status(data)

        if code in (401, 407):
            challenge = _parse_auth_challenge(data)
            if challenge is None:
                return {"ok": False, "detail": f"{code} ohne verwertbaren Auth-Header."}
            header_name, params = challenge
            auth = _digest_header(params, username, password, "REGISTER", uri)
            second = _build_sip_request(
                "REGISTER", host, port, "tls" if use_tls else transport, local_ip, local_port,
                call_id=call_id, from_tag=from_tag, branch=_rand_token(8), cseq=2, user=username,
                extra_headers=[f"{header_name}: {auth}"],
            )
            sock.sendall(second)
            data = sock.recv(65535)
            code = _parse_status(data)

        if code == 200:
            return {"ok": True, "detail": "200 OK – Registrierung erfolgreich."}
        if code in (401, 403, 407):
            return {"ok": False, "detail": f"{code} – Zugangsdaten abgelehnt."}
        return {"ok": False, "detail": f"Unerwarteter SIP-Status: {code}."}
    except TimeoutError:
        return {"ok": False, "detail": "Keine Antwort auf REGISTER (Timeout)."}
    except OSError as exc:
        return {"ok": False, "detail": f"REGISTER fehlgeschlagen: {exc}"}
    finally:
        with contextlib.suppress(OSError):
            sock.close()


# --------------------------------------------------------------------------- #
# Orchestrierung
# --------------------------------------------------------------------------- #

def _parse_ports(raw: str) -> list[int]:
    ports: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.isdigit() and 1 <= int(chunk) <= 65535:
            ports.append(int(chunk))
    return ports


def run_test(
    db: Session,
    server: VoipServer,
    *,
    source: str = "manual",
    username: str | None = None,
    password: str | None = None,
) -> VoipTest:
    """Führt alle passenden Teilprüfungen aus und persistiert ein ``VoipTest``."""
    try:
        host = validate_network_target(server.host)
    except ValueError as exc:
        test = VoipTest(server_id=server.id, source=source, ok=False, error=str(exc))
        db.add(test)
        db.commit()
        db.refresh(test)
        return test

    errors: list[str] = []

    # SIP-OPTIONS-Ping je Transport (UDP immer, TCP immer als Info).
    udp = sip_options_ping(host, server.sip_port, "udp")
    tcp = sip_options_ping(host, server.sip_port, "tcp")
    udp_ok, tcp_ok = udp["reachable"], tcp["reachable"]
    udp_code = udp["status_code"]

    # Antwortzeit/Statuscode aus dem konfigurierten Transport.
    opt = udp if server.sip_transport == "udp" else tcp
    if opt["error"] and not opt["reachable"]:
        errors.append(f"SIP-OPTIONS ({server.sip_transport}): {opt['error']}")

    # SIP over TLS
    tls_ok: bool | None = None
    tls_ms: float | None = None
    tls_verified: bool | None = None
    tls_expiry: datetime | None = None
    if server.sip_tls_port and server.sip_tls_port > 0:
        tls = check_tls(host, server.sip_tls_port)
        tls_ok = tls["ok"]
        tls_ms = tls["handshake_ms"]
        tls_verified = tls["verified"]
        tls_expiry = tls["cert_expiry"]
        if tls["error"]:
            errors.append(f"SIP-TLS ({server.sip_tls_port}): {tls['error']}")

    # WebRTC-/HTTPS-Ports
    webrtc: list[dict] = []
    for wport in _parse_ports(server.webrtc_ports):
        ok, ms = check_tcp_port(host, wport)
        webrtc.append({"port": wport, "ok": ok, "ms": ms})
        if not ok:
            errors.append(f"WebRTC-Port {wport} nicht erreichbar.")

    # ICMP-Qualität (best effort – ein Fehler hier darf den Test nicht abbrechen)
    loss_pct: float | None = None
    rtt_avg: float | None = None
    try:
        stats, ping_err = wan_service.measure(host, 10, 1000)
        if stats:
            loss_pct = stats.loss_pct
            rtt_avg = stats.rtt_avg
        elif ping_err:
            errors.append(f"ICMP: {ping_err}")
    except Exception as exc:  # noqa: BLE001 - ping/Parser-Fehler nicht fatal
        errors.append(f"ICMP nicht auswertbar: {exc}")

    # Optionaler REGISTER-Test
    reg_ok: bool | None = None
    reg_detail: str | None = None
    if username and password:
        use_tls = bool(server.sip_tls_port and server.sip_tls_port > 0)
        reg_port = server.sip_tls_port if use_tls else server.sip_port
        reg = sip_register_test(
            host, reg_port,
            "tls" if use_tls else server.sip_transport,
            username, password, use_tls=use_tls,
        )
        reg_ok = reg["ok"]
        reg_detail = reg["detail"]

    # Gesamturteil: SIP erreichbar (irgendein Transport) und – falls TLS-Port
    # gesetzt – TLS-Handshake ok. WebRTC/ICMP fließen als Warnungen ein, nicht als
    # hartes Kriterium.
    sip_reachable = bool(udp_ok or tcp_ok or opt["reachable"])
    overall = sip_reachable and (tls_ok is None or bool(tls_ok))
    if username and password:
        overall = overall and bool(reg_ok)

    test = VoipTest(
        server_id=server.id,
        source=source,
        ok=overall,
        sip_udp_ok=udp_ok,
        sip_tcp_ok=tcp_ok,
        sip_tls_ok=tls_ok,
        webrtc_results_json=json.dumps(webrtc),
        sip_response_ms=opt["response_ms"],
        sip_response_code=opt["status_code"] or udp_code,
        tls_handshake_ms=tls_ms,
        tls_verified=tls_verified,
        tls_cert_expiry=tls_expiry,
        loss_pct=loss_pct,
        rtt_avg_ms=rtt_avg,
        registration_ok=reg_ok,
        registration_detail=reg_detail,
        error="; ".join(errors) if errors else None,
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    log.info("VoIP-Test Server=%s ok=%s (source=%s)", server.id, overall, source)
    return test
