"""Reine Parser für SIP-/SDP-/RTP-Analyse aus tshark-Ausgaben.

Ohne Subprozess-/DB-Zugriff – vollständig per Unit-Test abdeckbar (wie
``tshark_parsers``).
"""

from __future__ import annotations

import re
from collections import Counter

# RTP-Payload-Typen (statischer Teil, RFC 3551) -> Codec-Kurzname.
PAYLOAD_TYPES: dict[int, str] = {
    0: "PCMU", 3: "GSM", 4: "G723", 8: "PCMA", 9: "G722",
    13: "CN", 18: "G729",
}

# Grobe Bandbreitenschätzung je Codec in kbit/s inkl. IP/UDP/RTP-Overhead
# (typisch bei 20 ms ptime). Bewusst Näherungswerte.
CODEC_BANDWIDTH_KBIT: dict[str, int] = {
    "PCMU": 87, "PCMA": 87, "G722": 87,
    "G729": 31, "G723": 27, "ILBC": 31,
    "GSM": 29, "OPUS": 50, "SPEEX": 40,
    "AMR": 25, "AMR-WB": 32,
}

_RTPMAP_RE = re.compile(r"rtpmap:(\d+)\s+([A-Za-z0-9._-]+)/(\d+)(?:/(\d+))?", re.I)
_FMTP_RE = re.compile(r"fmtp:(\d+)\s+([^\r\n,]+)", re.I)
_PTIME_RE = re.compile(r"ptime:(\d+)", re.I)
_DIRECTION_RE = re.compile(r"\b(sendrecv|sendonly|recvonly|inactive)\b", re.I)

# Reihenfolge MUSS zu SIP_FIELDS in voip_analysis.py passen.
_N_FIELDS = 14


def _cell(cols: list[str], idx: int) -> str:
    return cols[idx].strip() if idx < len(cols) else ""


def parse_sip_messages(lines: list[str]) -> dict:
    """Wertet die tab-getrennten ``tshark -T fields``-Zeilen des SIP-Passes aus."""
    by_method: Counter[str] = Counter()
    by_status_class: Counter[str] = Counter()
    errors: list[dict] = []
    retransmissions = 0
    server_headers: set[str] = set()
    allow_methods: set[str] = set()
    dialogs: dict[str, dict] = {}

    rtpmaps: list[str] = []
    fmtps: list[str] = []
    ptimes: list[int] = []
    directions: set[str] = set()
    static_payloads: set[int] = set()
    media_types: set[str] = set()
    secure_rtp = False
    count = 0

    for raw in lines:
        if not raw.strip():
            continue
        cols = raw.split("\t")
        count += 1
        ts = _cell(cols, 0)
        src, dst = _cell(cols, 1), _cell(cols, 2)
        method = _cell(cols, 3)
        status = _cell(cols, 4)
        status_line = _cell(cols, 5)
        call_id = _cell(cols, 6)
        cseq = _cell(cols, 7)
        server = _cell(cols, 8)
        user_agent = _cell(cols, 9)
        allow = _cell(cols, 10)
        resend = _cell(cols, 11)
        sdp_media = _cell(cols, 12)
        sdp_attr = _cell(cols, 13)

        if method:
            by_method[method.upper()] += 1
        if status:
            code = re.sub(r"[^0-9].*$", "", status.split(",")[0]) or status
            if code.isdigit():
                by_status_class[f"{code[0]}xx"] += 1
                if int(code) >= 400:
                    errors.append({"code": int(code), "reason": status_line or "",
                                   "ts": ts, "src": src, "dst": dst})
        if resend.strip() in ("1", "True", "true"):
            retransmissions += 1
        for hv in (server, user_agent):
            for part in hv.split(","):
                part = part.strip()
                if part:
                    server_headers.add(part)
        for am in allow.replace(",", " ").split():
            if am.isalpha():
                allow_methods.add(am.upper())

        if call_id:
            d = dialogs.setdefault(call_id, {"call_id": call_id, "cseq_nums": [], "methods": set()})
            if method:
                d["methods"].add(method.upper())
            cm = re.match(r"\s*(\d+)", cseq)
            if cm:
                d["cseq_nums"].append(int(cm.group(1)))

        for chunk in sdp_media.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split()
            if parts:
                media_types.add(parts[0].lower())
            if "SAVP" in chunk.upper():
                secure_rtp = True
            for tok in parts[3:] if len(parts) > 3 else []:
                if tok.isdigit():
                    static_payloads.add(int(tok))
        for rm in _RTPMAP_RE.finditer(sdp_attr):
            name = f"{rm.group(2)}/{rm.group(3)}" + (f"/{rm.group(4)}" if rm.group(4) else "")
            if name not in rtpmaps:
                rtpmaps.append(name)
        for fm in _FMTP_RE.finditer(sdp_attr):
            fmtps.append(fm.group(0))
        for pm in _PTIME_RE.finditer(sdp_attr):
            ptimes.append(int(pm.group(1)))
        for dm in _DIRECTION_RE.finditer(sdp_attr):
            directions.add(dm.group(1).lower())
        if "SAVPF" in sdp_attr.upper() or "crypto:" in sdp_attr.lower() or "dtls" in sdp_attr.lower():
            secure_rtp = True

    dialog_list = [
        {
            "call_id": d["call_id"],
            "methods": sorted(d["methods"]),
            "cseq_ok": d["cseq_nums"] == sorted(d["cseq_nums"]),
        }
        for d in dialogs.values()
    ]

    return {
        "messages_captured": count,
        "by_method": dict(by_method),
        "by_status_class": dict(by_status_class),
        "errors": errors[:100],
        "error_count": len(errors),
        "retransmissions": retransmissions,
        "dialogs": dialog_list,
        "dialog_count": len(dialog_list),
        "server_headers": sorted(server_headers),
        "allow_methods": sorted(allow_methods),
        "media_types": sorted(media_types),
        "rtpmaps": rtpmaps,
        "fmtps": fmtps[:50],
        "ptimes": sorted(set(ptimes)),
        "directions": sorted(directions),
        "static_payloads": sorted(static_payloads),
        "secure_rtp": secure_rtp,
    }


def _base_codec(name: str) -> str:
    return name.split("/")[0].upper()


def codec_summary(sip: dict) -> dict:
    """Baut aus dem SIP-Analyse-Dict eine Codec-Zusammenfassung."""
    offered: list[str] = list(sip.get("rtpmaps") or [])
    for pt in sip.get("static_payloads") or []:
        if pt in PAYLOAD_TYPES:
            label = PAYLOAD_TYPES[pt]
            if not any(_base_codec(c) == label for c in offered):
                offered.append(label)

    primary = offered[0] if offered else None
    bw = None
    if primary:
        bw = CODEC_BANDWIDTH_KBIT.get(_base_codec(primary))
    ptimes = sip.get("ptimes") or []
    return {
        "codecs_offered": offered,
        "primary_codec": primary,
        "estimated_bandwidth_kbit": bw,
        "ptime_ms": ptimes[0] if ptimes else None,
        "secure": bool(sip.get("secure_rtp")),
        "directions": sip.get("directions") or [],
    }


_RTP_ROW_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+"
    r"(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+"
    r"(\S+)\s+(\S+)\s+(\d+)\s+(-?\d+)\s+\(\s*([-\d.]+)%\)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)(.*)$"
)


def _codec_for_payload(payload: str) -> str:
    p = payload.strip()
    if p.isdigit() and int(p) in PAYLOAD_TYPES:
        return PAYLOAD_TYPES[int(p)]
    # tshark schreibt teils Namen wie "g711U", "ITU-T G.711 PCMU", "Opus"
    up = p.upper()
    for key in ("PCMU", "PCMA", "G722", "G729", "OPUS", "GSM", "ILBC", "AMR"):
        if key in up.replace(" ", "").replace(".", "").replace("-", ""):
            return key
    if "G711U" in up:
        return "PCMU"
    if "G711A" in up:
        return "PCMA"
    return p or "unbekannt"


def parse_rtp_streams(text: str) -> list[dict]:
    """Parst die Tabelle von ``tshark -q -z rtp,streams``."""
    streams: list[dict] = []
    for line in text.splitlines():
        m = _RTP_ROW_RE.match(line)
        if not m:
            continue
        streams.append({
            "src": m.group(1),
            "src_port": int(m.group(2)),
            "dst": m.group(3),
            "dst_port": int(m.group(4)),
            "ssrc": m.group(5),
            "payload_type": m.group(6),
            "codec": _codec_for_payload(m.group(6)),
            "packets": int(m.group(7)),
            "lost": int(m.group(8)),
            "lost_pct": float(m.group(9)),
            "max_delta_ms": float(m.group(10)),
            "max_jitter_ms": float(m.group(11)),
            "mean_jitter_ms": float(m.group(12)),
            "problems": m.group(13).strip() or None,
        })
    return streams


def rtp_summary(streams: list[dict]) -> dict:
    if not streams:
        return {"streams_detected": 0, "jitter_estimate_ms": None,
                "packet_loss_percent": None, "payload_types": [], "total_packets": 0}
    return {
        "streams_detected": len(streams),
        "jitter_estimate_ms": round(max(s["mean_jitter_ms"] for s in streams), 2),
        "packet_loss_percent": round(max(s["lost_pct"] for s in streams), 2),
        "payload_types": sorted({s["codec"] for s in streams}),
        "total_packets": sum(s["packets"] for s in streams),
    }
