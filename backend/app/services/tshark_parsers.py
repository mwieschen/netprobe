"""Reine Parser für tshark-Ausgaben. Ohne Subprozess-/DB-Zugriff, damit
per Unit-Test abdeckbar."""

from __future__ import annotations

import json
import re
from collections import Counter


def parse_protocol_hierarchy(text: str) -> list[dict]:
    """Parst ``tshark -z io,phs``. Liefert Einträge mit Ebene, Protokoll,
    Frames und Bytes."""
    entries: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"^(\s*)(\S+)\s+frames:(\d+)\s+bytes:(\d+)", line)
        if not m:
            continue
        indent = len(m.group(1))
        entries.append(
            {
                "level": indent // 2,
                "protocol": m.group(2),
                "frames": int(m.group(3)),
                "bytes": int(m.group(4)),
            }
        )
    return entries


def parse_endpoints(text: str) -> list[dict]:
    """Parst ``tshark -z endpoints,ip``-Tabellen."""
    results: list[dict] = []
    for line in text.splitlines():
        # z.B.: "1.1.1.1   120   45000   60   22000   60   23000"
        m = re.match(
            r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
            line.strip(),
        )
        if not m:
            continue
        results.append(
            {
                "address": m.group(1),
                "packets": int(m.group(2)),
                "bytes": int(m.group(3)),
                "tx_packets": int(m.group(4)),
                "tx_bytes": int(m.group(5)),
                "rx_packets": int(m.group(6)),
                "rx_bytes": int(m.group(7)),
            }
        )
    results.sort(key=lambda r: r["bytes"], reverse=True)
    return results


def parse_conversations(text: str) -> list[dict]:
    """Parst ``tshark -z conv,tcp`` / ``conv,udp``-Tabellen."""
    results: list[dict] = []
    # Adresse:Port <-> Adresse:Port  Frames Bytes ...
    pat = re.compile(
        r"^(\S+?)\s+<->\s+(\S+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
    )
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        results.append(
            {
                "endpoint_a": m.group(1),
                "endpoint_b": m.group(2),
                "frames_ab": int(m.group(3)),
                "bytes_ab": int(m.group(4)),
                "frames_ba": int(m.group(5)),
                "bytes_ba": int(m.group(6)),
                "frames_total": int(m.group(7)),
                "bytes_total": int(m.group(8)),
            }
        )
    results.sort(key=lambda r: r["bytes_total"], reverse=True)
    return results


def parse_field_lines(text: str) -> list[str]:
    """Splittet die zeilenweise Feld-Ausgabe von ``tshark -T fields``."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def aggregate_dns_queries(lines: list[str]) -> list[dict]:
    """Zählt DNS-Query-Namen (aus ``-e dns.qry.name``)."""
    counter: Counter[str] = Counter()
    for line in lines:
        for name in line.split(","):
            name = name.strip()
            if name:
                counter[name] += 1
    return [{"name": n, "count": c} for n, c in counter.most_common()]


def aggregate_tls_sni(lines: list[str]) -> list[dict]:
    """Zählt TLS-SNI-Hostnamen (aus ``-e tls.handshake.extensions_server_name``)."""
    counter: Counter[str] = Counter()
    for line in lines:
        for name in line.split(","):
            name = name.strip()
            if name:
                counter[name] += 1
    return [{"server_name": n, "count": c} for n, c in counter.most_common()]


def parse_expert_info(text: str) -> list[dict]:
    """Parst ``tshark -z expert``. Liefert Schweregrad, Gruppe, Protokoll,
    Anzahl und Meldung."""
    results: list[dict] = []
    current_severity = None
    for line in text.splitlines():
        stripped = line.strip()
        sev_m = re.match(r"^(Errors|Warnings|Notes|Chats)\s*\(?", stripped)
        if sev_m:
            current_severity = sev_m.group(1)
            continue
        # z.B.: "     Sequence  TCP   Retransmission ... 42"
        m = re.match(r"^(\S+)\s+(\S+)\s+(.+?)\s+(\d+)$", stripped)
        if m and current_severity:
            results.append(
                {
                    "severity": current_severity,
                    "group": m.group(1),
                    "protocol": m.group(2),
                    "summary": m.group(3).strip(),
                    "count": int(m.group(4)),
                }
            )
    return results


def count_json_packets(raw: str) -> int:
    """Zählt Pakete in einer ``tshark -T json``-Ausgabe."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    return len(data) if isinstance(data, list) else 0


# --- I/O-Statistik über Zeit ------------------------------------------------


def parse_epoch_len_lines(text: str) -> list[tuple[float, int]]:
    """Parst die Ausgabe von ``tshark -T fields -e frame.time_epoch -e frame.len``."""
    rows: list[tuple[float, int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split()
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), int(float(parts[1]))))
        except ValueError:
            continue
    return rows


_BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"


def parse_capture_metrics(text: str, top_n: int = 20) -> dict:
    """Wertet einen einzelnen ``tshark -T fields``-Durchlauf aus mit den Feldern
    (in dieser Reihenfolge, Tab-getrennt)::

        eth.dst.ig  eth.dst  frame.len  _ws.col.Protocol  ip.src  ip.dst  tcp.flags

    Liefert: Frame-Verteilung (Unicast/Broadcast/Multicast), Top-Protokolle
    (Pakete + Bytes), Top-IP-Conversations (Pakete + Bytes) sowie die Anzahl der
    TCP-Pakete.
    """
    uni = bcast = mcast = other = 0
    proto_pkts: Counter[str] = Counter()
    proto_bytes: Counter[str] = Counter()
    conv_pkts: Counter[tuple[str, str]] = Counter()
    conv_bytes: Counter[tuple[str, str]] = Counter()
    tcp_packets = 0
    analyzed = 0

    for raw in text.splitlines():
        if not raw.strip():
            continue
        analyzed += 1
        cols = raw.split("\t")
        cols += [""] * (7 - len(cols))
        ig, dst, length, proto, ip_src, ip_dst, tcp_flags = (c.strip() for c in cols[:7])
        dst = dst.lower()

        if dst == _BROADCAST_MAC:
            bcast += 1
        elif ig == "1":
            mcast += 1
        elif dst:
            uni += 1
        else:
            other += 1

        try:
            blen = int(length)
        except ValueError:
            blen = 0

        if proto:
            proto_pkts[proto] += 1
            proto_bytes[proto] += blen

        if ip_src and ip_dst:
            key = (ip_src, ip_dst) if ip_src <= ip_dst else (ip_dst, ip_src)
            conv_pkts[key] += 1
            conv_bytes[key] += blen

        if tcp_flags:
            tcp_packets += 1

    total = uni + bcast + mcast + other

    def pct(n: int) -> float:
        return round(n / total * 100, 2) if total else 0.0

    top_protocols = [
        {"protocol": p, "packets": c, "bytes": proto_bytes[p]}
        for p, c in proto_pkts.most_common(top_n)
    ]
    top_conversations = [
        {"addr_a": a, "addr_b": b, "packets": conv_pkts[(a, b)], "bytes": conv_bytes[(a, b)]}
        for (a, b), _ in conv_pkts.most_common(top_n)
    ]

    return {
        "packets_analyzed": analyzed,
        "frame_distribution": {
            "total": total,
            "unicast": uni,
            "broadcast": bcast,
            "multicast": mcast,
            "other": other,
            "unicast_pct": pct(uni),
            "broadcast_pct": pct(bcast),
            "multicast_pct": pct(mcast),
            "other_pct": pct(other),
        },
        "top_protocols": top_protocols,
        "top_conversations": top_conversations,
        "tcp_packets": tcp_packets,
    }


def compute_io_buckets(rows: list[tuple[float, int]], buckets: int = 120) -> dict:
    """Aggregiert ``(epoch, frame_len)``-Paare in ``buckets`` gleich lange Intervalle
    und liefert eine Zeitreihe (Pakete/s, Bit/s) plus Gesamtwerte."""
    buckets = max(1, min(buckets, 2000))
    empty: dict = {
        "first_ts": None,
        "last_ts": None,
        "duration_s": 0.0,
        "total_packets": 0,
        "total_bytes": 0,
        "avg_pps": 0.0,
        "avg_bps": 0.0,
        "interval_s": 0.0,
        "series": [],
    }
    if not rows:
        return empty

    times = [r[0] for r in rows]
    t0, t1 = min(times), max(times)
    span = max(t1 - t0, 1e-6)
    interval = span / buckets

    counts = [0] * buckets
    byte_sums = [0] * buckets
    for epoch, length in rows:
        idx = int((epoch - t0) / interval)
        idx = 0 if idx < 0 else min(idx, buckets - 1)
        counts[idx] += 1
        byte_sums[idx] += max(0, length)

    series = [
        {
            "t_epoch": round(t0 + i * interval, 3),
            "packets_per_s": round(counts[i] / interval, 2),
            "bits_per_s": round(byte_sums[i] * 8 / interval, 2),
        }
        for i in range(buckets)
    ]
    total_packets = sum(counts)
    total_bytes = sum(byte_sums)
    return {
        "first_ts": round(t0, 3),
        "last_ts": round(t1, 3),
        "duration_s": round(span, 3),
        "total_packets": total_packets,
        "total_bytes": total_bytes,
        "avg_pps": round(total_packets / span, 2),
        "avg_bps": round(total_bytes * 8 / span, 2),
        "interval_s": round(interval, 4),
        "series": series,
    }
