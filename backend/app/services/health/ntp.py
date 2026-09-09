"""NTP-/SNTP-Zeitabweichungstest (Standardbibliothek).

Parameter (``params``):
- ``max_offset_ms``: zulässige Abweichung (Standard 1000 ms).
"""

from __future__ import annotations

import socket
import struct
import time

from app.services.health import HealthOutcome, register

_TIMEOUT = 4.0
# Sekunden zwischen NTP-Epoche (1900-01-01) und Unix-Epoche (1970-01-01).
_NTP_DELTA = 2208988800.0


def _to_unix(ntp_seconds: float) -> float:
    return ntp_seconds - _NTP_DELTA


def run(target: str, params: dict) -> HealthOutcome:
    max_offset_ms = float(params.get("max_offset_ms", 1000))
    host = target.split(":")[0]

    packet = b"\x1b" + 47 * b"\0"  # LI=0, VN=3, Mode=3 (client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_TIMEOUT)
        t1 = time.time()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(48)
        t4 = time.time()

    if len(data) < 48:
        return HealthOutcome(ok=False, error="Unvollständige NTP-Antwort")

    # !B B b b 11I  -> [0]=LI/VN/Mode [1]=stratum [2]=poll [3]=precision
    #   [4]=root delay [5]=root disp [6]=ref id [7..8]=ref ts [9..10]=orig ts
    #   [11..12]=receive ts (T2) [13..14]=transmit ts (T3)
    fields = struct.unpack("!B B b b 11I", data)
    li_vn_mode, stratum, poll, precision = fields[0], fields[1], fields[2], fields[3]
    t2 = _to_unix(fields[11] + fields[12] / 2**32)
    t3 = _to_unix(fields[13] + fields[14] / 2**32)

    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)
    offset_ms = round(offset * 1000, 2)
    root_delay = fields[4] / 2**16 * 1000
    root_disp = fields[5] / 2**16 * 1000

    details = {
        "stratum": stratum,
        "precision_s": 2.0**precision,
        "poll_s": 2**poll if poll > 0 else None,
        "offset_ms": offset_ms,
        "round_trip_ms": round(delay * 1000, 2),
        "root_delay_ms": round(root_delay, 2),
        "root_dispersion_ms": round(root_disp, 2),
        "leap_indicator": li_vn_mode >> 6,
    }
    ok = 1 <= stratum <= 15 and abs(offset_ms) <= max_offset_ms and (li_vn_mode >> 6) != 3
    sign = "+" if offset_ms >= 0 else ""
    return HealthOutcome(
        ok=ok,
        response_ms=round(delay * 1000, 2),
        status=f"Stratum {stratum}, Offset {sign}{offset_ms} ms",
        details=details,
        error=None if ok else f"Zeitabweichung {offset_ms} ms > {max_offset_ms} ms oder ungültiger Stratum",
    )


register("ntp", run)
