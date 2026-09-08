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
