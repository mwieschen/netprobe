"""DNS-Auflösungstest.

Parameter (``params``):
- ``record_type``: ``A`` (Standard), ``AAAA``, ``MX``, ``TXT``, ``CNAME``, ``NS``.
- ``servers``: Liste von DNS-Servern (IP). ``""`` = System-Resolver (nur A/AAAA).
- ``expected``: erwarteter Wert (Substring-Vergleich gegen die Antworten), optional.

Der DNS-Query wird für explizite Server handgebaut (Standardbibliothek, kein
zusätzliches Paket).
"""

from __future__ import annotations

import secrets
import socket
import struct
import time

from app.services.health import HealthOutcome, register

_QTYPE = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}
_TIMEOUT = 4.0


def _build_query(qname: str, qtype: int) -> tuple[bytes, int]:
    tid = secrets.randbits(16)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)  # RD=1
    q = b"".join(
        len(part).to_bytes(1, "big") + part.encode("idna") for part in qname.rstrip(".").split(".")
    ) + b"\x00"
    return header + q + struct.pack(">HH", qtype, 1), tid


def _decode_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    end = offset
    for _ in range(128):
        length = data[offset]
        if length & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[offset : offset + 2])[0] & 0x3FFF
            if not jumped:
                end = offset + 2
            offset = ptr
            jumped = True
            continue
        offset += 1
        if length == 0:
            break
        labels.append(data[offset : offset + length].decode("latin1"))
        offset += length
    if not jumped:
        end = offset
    return ".".join(labels), end


def _parse_answers(data: bytes, want_type: int) -> list[str]:
    tid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    rcode = flags & 0x0F
    if rcode != 0:
        raise ValueError({0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN",
                          5: "REFUSED"}.get(rcode, f"RCODE {rcode}"))
    offset = 12
    for _ in range(qd):
        _, offset = _decode_name(data, offset)
        offset += 4
    out: list[str] = []
    for _ in range(an):
        _, offset = _decode_name(data, offset)
        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlen]
        if rtype == 1 and rdlen == 4:
            out.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == 28 and rdlen == 16:
            out.append(socket.inet_ntop(socket.AF_INET6, rdata))
        elif rtype in (2, 5, 12):
            name, _ = _decode_name(data, offset)
            out.append(name)
        elif rtype == 15:
            pref = struct.unpack(">H", rdata[:2])[0]
            name, _ = _decode_name(data, offset + 2)
            out.append(f"{pref} {name}")
        elif rtype == 16:
            txt, i = [], 0
            while i < len(rdata):
                ln = rdata[i]
                txt.append(rdata[i + 1 : i + 1 + ln].decode("latin1"))
                i += 1 + ln
            out.append("".join(txt))
        offset += rdlen
    _ = (tid, rclass, ttl, ns, ar, want_type)
    return out


def _query_server(server: str, qname: str, qtype: int) -> tuple[list[str], float]:
    packet, _ = _build_query(qname, qtype)
    start = time.perf_counter()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_TIMEOUT)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    return _parse_answers(data, qtype), round((time.perf_counter() - start) * 1000, 2)


def _system_resolve(qname: str, family: int) -> tuple[list[str], float]:
    start = time.perf_counter()
    infos = socket.getaddrinfo(qname, None, family)
    ms = round((time.perf_counter() - start) * 1000, 2)
    return sorted({str(i[4][0]) for i in infos}), ms


def run(target: str, params: dict) -> HealthOutcome:
    rtype = str(params.get("record_type", "A")).upper()
    qtype = _QTYPE.get(rtype, 1)
    servers = params.get("servers") or [""]
    expected = str(params.get("expected", "")).strip()

    per_server: dict = {}
    times: list[float] = []
    any_ok = False
    errors: list[str] = []

    for server in servers:
        label = server or "System-Resolver"
        try:
            if server:
                answers, ms = _query_server(server, target, qtype)
            elif rtype in ("A", "AAAA"):
                fam = socket.AF_INET if rtype == "A" else socket.AF_INET6
                answers, ms = _system_resolve(target, fam)
            else:
                errors.append(f"{label}: {rtype} braucht einen expliziten DNS-Server")
                per_server[label] = {"error": "kein Server"}
                continue
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            per_server[label] = {"error": str(exc)}
            continue

        times.append(ms)
        match = (not expected) or any(expected in a for a in answers)
        per_server[label] = {"answers": answers, "response_ms": ms, "match": match}
        if answers and match:
            any_ok = True
        elif answers and not match:
            errors.append(f"{label}: erwarteter Wert {expected!r} nicht in {answers}")

    answered = sum(1 for v in per_server.values() if v.get("answers"))
    status = f"{rtype}: {answered}/{len(servers)} Server geantwortet"
    return HealthOutcome(
        ok=any_ok,
        response_ms=round(sum(times) / len(times), 2) if times else None,
        status=status,
        details={"record_type": rtype, "expected": expected or None, "servers": per_server},
        error="; ".join(errors) if errors and not any_ok else None,
    )


register("dns", run)
