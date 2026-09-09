"""Reine Parser- und Berechnungsfunktionen ohne Seiteneffekte.

Diese Funktionen sind bewusst frei von Subprozess-/DB-Zugriffen, damit sie
vollständig per Unit-Test abgedeckt werden können."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field


@dataclass
class SpeedtestParsed:
    ok: bool
    download_mbps: float | None = None
    upload_mbps: float | None = None
    ping_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss: float | None = None
    server_id: int | None = None
    server_name: str | None = None
    error: str | None = None


def parse_speedtest_json(raw: str) -> SpeedtestParsed:
    """Parst die JSON-Ausgabe der Ookla Speedtest CLI (``--format=json``).

    Bandbreite wird von der CLI in Bytes/s geliefert; Umrechnung in Mbps:
    ``bytes_per_s * 8 / 1_000_000``.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return SpeedtestParsed(ok=False, error=f"Ungültiges JSON: {exc}")

    if data.get("type") == "log" or "download" not in data:
        # Fehlermeldung der CLI
        msg = data.get("message") or data.get("error") or "Unbekannter Speedtest-Fehler"
        return SpeedtestParsed(ok=False, error=str(msg))

    def _mbps(section: dict | None) -> float | None:
        if not section or "bandwidth" not in section:
            return None
        return round(section["bandwidth"] * 8 / 1_000_000, 3)

    ping = data.get("ping") or {}
    server = data.get("server") or {}
    packet_loss = data.get("packetLoss")

    return SpeedtestParsed(
        ok=True,
        download_mbps=_mbps(data.get("download")),
        upload_mbps=_mbps(data.get("upload")),
        ping_ms=_round(ping.get("latency")),
        jitter_ms=_round(ping.get("jitter")),
        packet_loss=float(packet_loss) if packet_loss is not None else None,
        server_id=server.get("id"),
        server_name=_server_name(server),
    )


def _server_name(server: dict) -> str | None:
    parts = [server.get("name"), server.get("location"), server.get("country")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _round(value: float | None, ndigits: int = 3) -> float | None:
    return round(float(value), ndigits) if value is not None else None


# --- RTT-Statistik -----------------------------------------------------------


@dataclass
class RttStats:
    loss_pct: float
    rtt_min: float | None = None
    rtt_avg: float | None = None
    rtt_max: float | None = None
    rtt_stddev: float | None = None
    jitter_ms: float | None = None
    rtts: list[float] = field(default_factory=list)


def compute_jitter(rtts: list[float]) -> float | None:
    """Jitter = Mittelwert der absoluten Differenzen aufeinanderfolgender RTTs."""
    if len(rtts) < 2:
        return None
    diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
    return round(sum(diffs) / len(diffs), 3)


def compute_rtt_stats(rtts: list[float], sent: int) -> RttStats:
    """Berechnet Statistik aus einer Liste erfolgreicher RTTs (ms) und der
    Anzahl gesendeter Pakete."""
    received = len(rtts)
    loss_pct = round((sent - received) / sent * 100, 2) if sent > 0 else 100.0

    if not rtts:
        return RttStats(loss_pct=loss_pct, rtts=[])

    avg = sum(rtts) / received
    if received > 1:
        variance = sum((x - avg) ** 2 for x in rtts) / received
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0

    return RttStats(
        loss_pct=loss_pct,
        rtt_min=round(min(rtts), 3),
        rtt_avg=round(avg, 3),
        rtt_max=round(max(rtts), 3),
        rtt_stddev=round(stddev, 3),
        jitter_ms=compute_jitter(rtts),
        rtts=rtts,
    )


# --- fping / ping Ausgabe ----------------------------------------------------

# fping -C <n> -q gibt je Ziel eine Zeile auf stderr aus, z.B.:
#   1.1.1.1 : 12.3 8.9 - 10.1 9.7
# "-" markiert einen Timeout.
_FPING_LINE = re.compile(r"^\S+\s*:\s*(.+)$")


def parse_fping_output(stderr: str, sent: int) -> RttStats:
    """Parst die ``fping -C <n> -q``-Ausgabe (auf stderr)."""
    rtts: list[float] = []
    for line in stderr.splitlines():
        m = _FPING_LINE.match(line.strip())
        if not m:
            continue
        for tok in m.group(1).split():
            if tok == "-":
                continue
            try:
                rtts.append(float(tok))
            except ValueError:
                continue
    return compute_rtt_stats(rtts, sent)


# ping (iputils) Zeilen: "64 bytes from 1.1.1.1: icmp_seq=1 ttl=59 time=12.3 ms"
_PING_TIME = re.compile(r"time[=<]\s*([\d.]+)\s*ms")


def parse_ping_output(stdout: str, sent: int) -> RttStats:
    """Parst die Standardausgabe von ``ping`` (iputils)."""
    rtts = [float(m.group(1)) for m in _PING_TIME.finditer(stdout)]
    return compute_rtt_stats(rtts, sent)


def parse_ping_single(stdout: str) -> float | None:
    """Extrahiert die RTT eines einzelnen Ping-Aufrufs; None bei Timeout."""
    m = _PING_TIME.search(stdout)
    return float(m.group(1)) if m else None


# --- iperf3 ---------------------------------------------------------------


@dataclass
class Iperf3Parsed:
    ok: bool
    protocol: str | None = None
    sent_mbps: float | None = None
    recv_mbps: float | None = None
    jitter_ms: float | None = None
    lost_percent: float | None = None
    lost_packets: int | None = None
    packets: int | None = None
    out_of_order: int | None = None
    retransmits: int | None = None
    error: str | None = None


def parse_iperf3_json(raw: str) -> Iperf3Parsed:
    """Parst die JSON-Ausgabe von ``iperf3 -c … -J``.

    Durchsatz wird von iperf3 in Bit/s geliefert; Umrechnung in Mbit/s = ``/ 1e6``.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return Iperf3Parsed(ok=False, error=f"Ungültiges JSON: {exc}")

    if isinstance(data, dict) and data.get("error"):
        return Iperf3Parsed(ok=False, error=str(data["error"]))

    end = data.get("end") or {}
    start = data.get("start") or {}
    proto = ((start.get("test_start") or {}).get("protocol") or "").lower() or None

    sum_sent = end.get("sum_sent") or {}
    sum_recv = end.get("sum_received") or {}
    udp_sum = end.get("sum") or {}

    sent_bps = sum_sent.get("bits_per_second") or udp_sum.get("bits_per_second")
    recv_bps = sum_recv.get("bits_per_second")
    retr = sum_sent.get("retransmits")

    def _int(v: object) -> int | None:
        return v if isinstance(v, int) else None

    return Iperf3Parsed(
        ok=True,
        protocol=proto,
        sent_mbps=round(sent_bps / 1_000_000, 3) if sent_bps else None,
        recv_mbps=round(recv_bps / 1_000_000, 3) if recv_bps else None,
        jitter_ms=_round(udp_sum.get("jitter_ms")),
        lost_percent=_round(udp_sum.get("lost_percent")),
        lost_packets=_int(udp_sum.get("lost_packets")),
        packets=_int(udp_sum.get("packets")),
        out_of_order=_int(udp_sum.get("out_of_order")),
        retransmits=_int(retr),
    )
