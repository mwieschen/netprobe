"""Unit-Tests für den VoIP-/3CX-Prüfdienst (ohne echte 3CX-Anlage)."""

from __future__ import annotations

import hashlib
import re
import socket
import threading

import pytest

from app.services import voip_service


@pytest.fixture
def sip_udp_server():
    """Lokaler UDP-Server, der auf jedes Datagramm mit ``SIP/2.0 200 OK`` antwortet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    stop = threading.Event()

    def serve():
        sock.settimeout(0.5)
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            if data:
                sock.sendto(b"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n", addr)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield port
    stop.set()
    t.join(timeout=2)
    sock.close()


def test_sip_options_ping_success(sip_udp_server):
    r = voip_service.sip_options_ping("127.0.0.1", sip_udp_server, "udp", timeout=2.0)
    assert r["reachable"] is True
    assert r["status_code"] == 200
    assert r["response_ms"] is not None
    assert r["error"] is None


def test_sip_options_ping_no_answer():
    # Port 9 (discard) antwortet nicht auf SIP -> reachable False, sauberer Fehlertext
    # (je nach OS Timeout oder Connection-Reset – beides ist ein sauberes Ergebnis).
    r = voip_service.sip_options_ping("127.0.0.1", 9, "udp", timeout=1.0)
    assert r["reachable"] is False
    assert r["status_code"] is None
    assert r["response_ms"] is None
    assert r["error"]


def test_check_tcp_port_open_and_closed():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    open_port = listener.getsockname()[1]
    try:
        ok, ms = voip_service.check_tcp_port("127.0.0.1", open_port, timeout=2.0)
        assert ok is True
        assert ms is not None
    finally:
        listener.close()

    # Derselbe Port ist jetzt zu.
    ok, ms = voip_service.check_tcp_port("127.0.0.1", open_port, timeout=1.0)
    assert ok is False
    assert ms is None


def test_parse_ports():
    assert voip_service._parse_ports("443, 5001 ,x, 70000, 22") == [443, 5001, 22]


def test_parse_status_line():
    assert voip_service._parse_status(b"SIP/2.0 401 Unauthorized\r\n") == 401
    assert voip_service._parse_status(b"garbage") is None


def test_digest_header_no_qop_matches_rfc_math():
    params = {"realm": "3cx", "nonce": "abc123", "algorithm": "MD5"}
    header = voip_service._digest_header(params, "alice", "secret", "REGISTER", "sip:pbx.example.com")

    def md5(s: str) -> str:
        return hashlib.md5(s.encode()).hexdigest()

    ha1 = md5("alice:3cx:secret")
    ha2 = md5("REGISTER:sip:pbx.example.com")
    expected = md5(f"{ha1}:abc123:{ha2}")

    assert header.startswith("Digest ")
    assert f'response="{expected}"' in header
    assert 'realm="3cx"' in header
    assert 'nonce="abc123"' in header


def test_parse_auth_challenge_www_authenticate():
    data = (
        b"SIP/2.0 401 Unauthorized\r\n"
        b'WWW-Authenticate: Digest realm="3cxphone", nonce="xyz", qop="auth", algorithm=MD5\r\n'
        b"\r\n"
    )
    header, params = voip_service._parse_auth_challenge(data)
    assert header == "Authorization"
    assert params["realm"] == "3cxphone"
    assert params["nonce"] == "xyz"
    assert params["qop"] == "auth"


def test_build_sip_request_is_crlf_and_well_formed():
    raw = voip_service._build_sip_request(
        "OPTIONS", "pbx.example.com", 5060, "udp", "10.0.0.5", 5062,
        call_id="cid@netprobe", from_tag="t1", branch="b1", cseq=1,
    )
    assert raw.endswith(b"\r\n\r\n")
    assert raw.replace(b"\r\n", b"") .count(b"\n") == 0  # keine nackten LF
    assert re.match(rb"OPTIONS sip:pbx\.example\.com SIP/2\.0\r\n", raw)
    assert b"Max-Forwards: 70\r\n" in raw
    assert b"Content-Length: 0\r\n" in raw
