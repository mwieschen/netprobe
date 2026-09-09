"""Tests für das Health-Check-Framework (ohne echte Netzwerkdienste)."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from app.services import health as hc
from app.services.health import dns as dns_mod
from app.services.health import ntp as ntp_mod


def test_registry_has_all_types():
    assert set(hc.available_types()) == {"dns", "http", "smtp", "imap", "ntp"}


def test_run_check_unknown_type():
    out = hc.run_check("gopher", "x", {})
    assert out.ok is False
    assert "Unbekannter Prüftyp" in out.error


# --- DNS -----------------------------------------------------------------
def _dns_response(query: bytes, ip: str = "93.184.216.34") -> bytes:
    tid = query[:2]
    # Frage-Sektion aus dem Query übernehmen (ab Byte 12 bis zum QTYPE/QCLASS-Ende)
    question = query[12:]
    header = tid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)  # QR=1, RD/RA
    answer = (
        b"\xc0\x0c"  # Pointer auf Offset 12 (QNAME)
        + struct.pack(">HHIH", 1, 1, 60, 4)  # TYPE=A CLASS=IN TTL=60 RDLEN=4
        + socket.inet_aton(ip)
    )
    return header + question + answer


@pytest.fixture
def dns_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    stop = threading.Event()

    def serve():
        sock.settimeout(0.3)
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            sock.sendto(_dns_response(data), addr)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield port
    stop.set()
    t.join(timeout=2)
    sock.close()


def test_dns_check_against_local_server(dns_server, monkeypatch):
    monkeypatch.setattr(dns_mod, "_TIMEOUT", 2.0)
    # Der handgebaute Query nutzt Port 53 fest -> temporär auf Testport umbiegen
    orig = dns_mod._query_server

    def patched(server, qname, qtype):
        packet, _ = dns_mod._build_query(qname, qtype)
        start = time.perf_counter()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2.0)
            s.sendto(packet, ("127.0.0.1", dns_server))
            data, _ = s.recvfrom(4096)
        return dns_mod._parse_answers(data, qtype), round((time.perf_counter() - start) * 1000, 2)

    monkeypatch.setattr(dns_mod, "_query_server", patched)
    try:
        out = hc.run_check("dns", "example.com", {"servers": ["127.0.0.1"], "expected": "93.184"})
        assert out.ok is True
        assert "93.184.216.34" in out.details["servers"]["127.0.0.1"]["answers"]
    finally:
        monkeypatch.setattr(dns_mod, "_query_server", orig)


def test_dns_parse_nxdomain():
    header = b"\x12\x34" + struct.pack(">HHHHH", 0x8183, 1, 0, 0, 0)  # RCODE 3
    question = b"\x03www\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    with pytest.raises(ValueError, match="NXDOMAIN"):
        dns_mod._parse_answers(header + question, 1)


# --- NTP -----------------------------------------------------------------
def test_ntp_parse_synthetic_packet(monkeypatch):
    now_ntp = time.time() + ntp_mod._NTP_DELTA

    def fake_socket(*a, **k):
        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def settimeout(self, _):
                pass

            def sendto(self, *_):
                pass

            def recvfrom(self, _):
                pkt = bytearray(48)
                pkt[0] = 0x1C  # LI=0 VN=3 Mode=4 (server)
                pkt[1] = 2  # stratum
                pkt[2] = 6  # poll
                pkt[3] = 0xE8  # precision (-24 signed)
                s = int(now_ntp)
                struct.pack_into("!I", pkt, 32, s)  # receive ts
                struct.pack_into("!I", pkt, 40, s)  # transmit ts
                return bytes(pkt), ("127.0.0.1", 123)

        return _S()

    monkeypatch.setattr(ntp_mod.socket, "socket", fake_socket)
    out = hc.run_check("ntp", "pool.ntp.org", {"max_offset_ms": 5000})
    assert out.details["stratum"] == 2
    assert out.status.startswith("Stratum 2")
    assert out.ok is True


# --- HTTP --------------------------------------------------------------
def test_http_check_mocked(monkeypatch):
    from app.services.health import http as http_mod

    class _Resp:
        status_code = 200
        reason_phrase = "OK"
        text = "<html>Willkommen bei netprobe</html>"
        url = "https://example.com/"

    monkeypatch.setattr(http_mod.httpx, "request", lambda *a, **k: _Resp())
    monkeypatch.setattr(http_mod, "_cert_days_left", lambda h, p: (200, "2027-01-01T00:00:00+00:00"))
    out = hc.run_check("http", "example.com", {"expect_substring": "netprobe"})
    assert out.ok is True
    assert out.details["status_code"] == 200


def test_http_check_substring_missing(monkeypatch):
    from app.services.health import http as http_mod

    class _Resp:
        status_code = 200
        reason_phrase = "OK"
        text = "nichts"
        url = "http://example.com/"

    monkeypatch.setattr(http_mod.httpx, "request", lambda *a, **k: _Resp())
    out = hc.run_check("http", "http://example.com", {"expect_substring": "fehlt"})
    assert out.ok is False
