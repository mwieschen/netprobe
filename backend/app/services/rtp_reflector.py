"""RTP-Reflektor: Gegenstelle für Sprachtests der Wachtl-Probes (Check-Typ ``rtp``, Gegenstelle „netprobe“).

Protokoll wie Wachtl ``server/wachtl/rtp`` (RFC-3550-Pakete, Messfelder ab Byte 12)::

    0–11 RTP-Kopf · 12–15 magic WTL1/WTLQ · 16–23 t1 (Probe) · 24–31 t2 (Reflektor) · 32–35 count

Der Reflektor überschreibt nur t2 und count und schickt das Paket in gleicher Größe zurück – keine
Verstärkung. Er antwortet nur auf Wachtl-RTP-Pakete und begrenzt die Rate je Absender und gesamt, damit
ein offener UDP-Port nicht als Verstärker für fremden Verkehr taugt. Läuft als asyncio-Endpunkt im
netprobe-Prozess; Zustand (an/aus, Port) im KV-Store, ``resume()`` beim Start (wie ``iperf3_server``).
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import time

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("netprobe.rtp")

HEADER = 12
MIN_PACKET = HEADER + 24
MAX_PACKET = 1500
MAGIC_DATA = b"WTL1"
MAGIC_QUERY = b"WTLQ"
DEFAULT_PORT = 5004
_FIELDS = struct.Struct(">4sqqI")

PER_SOURCE_PPS = 2000  # z. B. 20 Probes hinter einer NAT-Adresse mit 10-ms-Paketen
TOTAL_PPS = 20000
SESSION_IDLE_S = 120
MAX_SESSIONS = 50000

KV_ENABLED = "wachtl_rtp_enabled"
KV_PORT = "wachtl_rtp_port"


def parse(data: bytes) -> tuple[int, bool] | None:
    """Gültiges Wachtl-RTP-Paket → (SSRC, Abfrage?); sonst None."""
    if not MIN_PACKET <= len(data) <= MAX_PACKET or data[0] >> 6 != 2:
        return None
    magic = data[HEADER : HEADER + 4]
    if magic not in (MAGIC_DATA, MAGIC_QUERY):
        return None
    ssrc = struct.unpack_from(">I", data, 8)[0]
    return ssrc, magic == MAGIC_QUERY


def reflect(data: bytes, t2_us: int, count: int) -> bytes:
    """Antwort: gleiches Paket, gleiche Größe, mit Empfangszeit und Zähler."""
    magic, t1, _, _ = _FIELDS.unpack_from(data, HEADER)
    return data[:HEADER] + _FIELDS.pack(magic, t1, max(1, t2_us), count & 0xFFFFFFFF) + data[MIN_PACKET:]


class _Bucket:
    def __init__(self, rate: float, now: float) -> None:
        self.rate, self.tokens, self.at = rate, rate, now

    def take(self, now: float) -> bool:
        self.tokens = min(self.rate, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Reflector:
    """Zustand des Reflektors ohne Socket – testbar mit ``handle``."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.sessions: dict[tuple, list] = {}  # (Adresse, Port, SSRC) → [Zähler, zuletzt gesehen]
        self.sources: dict[str, _Bucket] = {}
        self.total = _Bucket(TOTAL_PPS, clock())
        self.next_cleanup = clock() + 10
        self.dropped = 0

    def handle(self, data: bytes, addr: tuple) -> bytes | None:
        now = self.clock()
        packet = parse(data)
        if packet is None:
            return None
        ssrc, query = packet
        bucket = self.sources.get(addr[0])
        if bucket is None:
            bucket = self.sources[addr[0]] = _Bucket(PER_SOURCE_PPS, now)
        if not (bucket.take(now) and self.total.take(now)):
            self.dropped += 1
            return None
        key = (addr[0], addr[1], ssrc)
        session = self.sessions.get(key)
        if session is None:
            if len(self.sessions) >= MAX_SESSIONS:
                self.dropped += 1
                return None
            session = self.sessions[key] = [0, now]
        if not query:
            session[0] += 1
        session[1] = now
        if now >= self.next_cleanup:
            self._cleanup(now)
        return reflect(data, int(now * 1_000_000), session[0])

    def _cleanup(self, now: float) -> None:
        self.next_cleanup = now + 10
        for key in [k for k, s in self.sessions.items() if now - s[1] > SESSION_IDLE_S]:
            del self.sessions[key]
        for ip in [ip for ip, b in self.sources.items() if now - b.at > SESSION_IDLE_S]:
            del self.sources[ip]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, reflector: Reflector) -> None:
        self.reflector = reflector
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        reply = self.reflector.handle(data, addr)
        if reply is not None and self.transport is not None:
            self.transport.sendto(reply, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug("RTP-Reflektor: %s", exc)


class RtpReflectorService:
    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.port: int | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._transport is not None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def start(self, port: int) -> None:
        await self.stop()
        loop = asyncio.get_running_loop()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            with contextlib.suppress(OSError):  # Antworten als Sprache (EF) markieren
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 46 << 2)
            transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(Reflector()), sock=sock)
        except OSError as exc:
            self.error = f"UDP-Port {port} nicht verfügbar: {exc}"
            log.error("RTP-Reflektor: %s", self.error)
            return
        self._transport = transport
        self.port = port
        self.error = None
        log.info("RTP-Reflektor lauscht auf UDP-Port %s", port)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            log.info("RTP-Reflektor beendet")

    def apply(self, enabled: bool, port: int) -> None:
        """Aus synchronem Code (Endpunkt im Threadpool) auf den Haupt-Loop übertragen."""
        if self._loop is None:
            return
        coro = self.start(port) if enabled else self.stop()
        asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=5)

    async def resume(self) -> None:
        from app.db.session import session_scope
        from app.services import kvstore, wachtl_service

        with session_scope() as db:
            wanted = wachtl_service.is_enabled(db) and bool(
                kvstore.get_value(db, KV_ENABLED, settings.wachtl_rtp_enabled)
            )
            port = int(kvstore.get_value(db, KV_PORT, settings.wachtl_rtp_port))
        if wanted:
            await self.start(port)

    async def shutdown(self) -> None:
        await self.stop()


rtp_reflector = RtpReflectorService()
