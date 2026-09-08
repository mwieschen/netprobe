"""Packet Capture über dumpcap und Basisanalyse über tshark.

Sicherheit:
- Ausschließlich Argumentlisten, niemals shell=True.
- Interface-Namen und BPF-Filter werden validiert.
- Capture-Dateien liegen außerhalb des Webroots (``settings.capture_dir``).
- Display-Filter laufen nur gegen vorhandene PCAPs, mit Timeout, Paging und Limit.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.models.capture import Capture
from app.services import tshark_parsers as tp
from app.services.proc import ToolNotFoundError, run_sync

log = get_logger("netprobe.capture")

# Interface-Namen: nur alphanumerisch, ., :, _, - (deckt eth0, wlan0, ens3, br-x ab)
_IFACE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
# BPF-Filter: konservative Whitelist zulässiger Zeichen
_BPF_RE = re.compile(r"^[A-Za-z0-9 ()\[\]<>=!&|.:/_-]{0,512}$")
# Display-Filter (tshark -Y): ebenfalls konservativ begrenzt
_DFILTER_RE = re.compile(r"^[A-Za-z0-9 ()\[\]<>=!&|.:/_\"'+*,-]{0,512}$")


class CaptureValidationError(ValueError):
    pass


def validate_iface(iface: str) -> str:
    if not _IFACE_RE.match(iface):
        raise CaptureValidationError(f"Ungültiger Interface-Name: {iface!r}")
    return iface


def validate_bpf(bpf: str | None) -> str | None:
    if bpf is None or bpf.strip() == "":
        return None
    bpf = bpf.strip()
    if not _BPF_RE.match(bpf):
        raise CaptureValidationError("BPF-Filter enthält unzulässige Zeichen.")
    return bpf


def validate_dfilter(dfilter: str) -> str:
    if not _DFILTER_RE.match(dfilter):
        raise CaptureValidationError("Display-Filter enthält unzulässige Zeichen.")
    return dfilter


def list_interfaces() -> list[dict]:
    """Listet Capture-Interfaces via ``dumpcap -D``."""
    try:
        proc = run_sync([settings.dumpcap_bin, "-D"], timeout=15)
    except ToolNotFoundError as exc:
        raise exc
    interfaces: list[dict] = []
    for line in proc.stdout.splitlines():
        # Format: "1. eth0 (Ethernet)"
        m = re.match(r"^\s*(\d+)\.\s+(\S+)(?:\s+\((.+)\))?", line)
        if m:
            interfaces.append({"index": int(m.group(1)), "name": m.group(2), "description": m.group(3)})
    return interfaces


def build_dumpcap_args(
    iface: str,
    out_path: Path,
    duration_s: int,
    max_packets: int | None,
    max_filesize_mb: int | None,
    ringbuffer: int | None,
    bpf: str | None,
) -> list[str]:
    args = [settings.dumpcap_bin, "-i", iface, "-w", str(out_path)]
    args += ["-a", f"duration:{duration_s}"]
    if max_packets:
        args += ["-c", str(max_packets)]
    if max_filesize_mb:
        # dumpcap erwartet KiB
        args += ["-b", f"filesize:{max_filesize_mb * 1024}"]
        if ringbuffer:
            args += ["-b", f"files:{ringbuffer}"]
    if bpf:
        args += ["-f", bpf]
    return args


def clamp_limits(duration_s: int, max_filesize_mb: int) -> tuple[int, int]:
    duration_s = max(1, min(duration_s, settings.capture_max_duration_s))
    max_filesize_mb = max(1, min(max_filesize_mb, settings.capture_max_size_mb))
    return duration_s, max_filesize_mb


def run_capture(db: Session, capture: Capture) -> Capture:
    """Führt einen Capture synchron aus (im Worker-/Threadpool-Kontext)."""
    settings.capture_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.capture_dir / f"capture_{capture.id}_{int(utcnow().timestamp())}.pcapng"

    try:
        iface = validate_iface(capture.iface)
        bpf = validate_bpf(capture.bpf)
        args = build_dumpcap_args(
            iface,
            out_path,
            capture.duration_s,
            capture.max_packets,
            capture.max_filesize_mb,
            capture.ringbuffer,
            bpf,
        )
        proc = run_sync(args, timeout=capture.duration_s + 30)
    except (CaptureValidationError, ToolNotFoundError) as exc:
        capture.status = "error"
        capture.error = str(exc)
        db.commit()
        return capture

    # dumpcap kann mit Ringbuffer eine indizierte Datei erzeugen
    actual = out_path
    if not actual.exists():
        # Ringbuffer-Modus: dumpcap hängt _00001_<ts> an
        candidates = sorted(settings.capture_dir.glob(f"capture_{capture.id}_*"))
        if candidates:
            actual = candidates[-1]

    if not actual.exists():
        capture.status = "error"
        capture.error = proc.stderr.strip() or "Capture erzeugte keine Datei."
        db.commit()
        return capture

    capture.path = str(actual)
    capture.size_bytes = actual.stat().st_size
    capture.packet_count = _count_packets(actual)
    capture.status = "done"
    db.commit()
    db.refresh(capture)
    log.info("Capture %s fertig: %s (%s Bytes)", capture.id, actual, capture.size_bytes)
    return capture


def _count_packets(path: Path) -> int | None:
    try:
        proc = run_sync(
            [settings.tshark_bin, "-r", str(path), "-q", "-z", "io,stat,0"],
            timeout=settings.tshark_timeout_s,
        )
    except ToolNotFoundError:
        return None
    m = re.search(r"\|\s*(\d+)\s*\|", proc.stdout)
    return int(m.group(1)) if m else None


def analyze(path: Path) -> dict:
    """Sichere Basisanalyse einer PCAP-Datei über tshark."""
    if not path.exists():
        raise FileNotFoundError(str(path))

    def _z(*z_args: str) -> str:
        proc = run_sync(
            [settings.tshark_bin, "-r", str(path), "-q", *z_args],
            timeout=settings.tshark_timeout_s,
        )
        return proc.stdout

    def _fields(*field_args: str) -> list[str]:
        args = [settings.tshark_bin, "-r", str(path), "-T", "fields"]
        for f in field_args:
            args += ["-e", f]
        proc = run_sync(args, timeout=settings.tshark_timeout_s)
        return tp.parse_field_lines(proc.stdout)

    protocol_hierarchy = tp.parse_protocol_hierarchy(_z("-z", "io,phs"))
    endpoints = tp.parse_endpoints(_z("-z", "endpoints,ip"))
    tcp_conv = tp.parse_conversations(_z("-z", "conv,tcp"))
    udp_conv = tp.parse_conversations(_z("-z", "conv,udp"))
    expert = tp.parse_expert_info(_z("-z", "expert"))

    dns = tp.aggregate_dns_queries(
        _fields_filtered(path, "dns.flags.response == 0", "dns.qry.name")
    )
    tls_sni = tp.aggregate_tls_sni(
        _fields_filtered(path, "tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name")
    )
    retrans = len(_fields_filtered(path, "tcp.analysis.retransmission", "frame.number"))
    resets = len(_fields_filtered(path, "tcp.flags.reset == 1", "frame.number"))

    total_packets = sum(e["frames"] for e in protocol_hierarchy if e["level"] == 0) or None

    return {
        "packet_count": total_packets,
        "protocol_hierarchy": protocol_hierarchy,
        "top_endpoints": endpoints[:50],
        "tcp_conversations": tcp_conv[:50],
        "udp_conversations": udp_conv[:50],
        "dns_queries": dns[:200],
        "tls_sni": tls_sni[:200],
        "tcp_retransmissions": retrans,
        "tcp_resets": resets,
        "expert_infos": expert,
    }


def _fields_filtered(path: Path, dfilter: str, *field_args: str) -> list[str]:
    args = [settings.tshark_bin, "-r", str(path), "-Y", dfilter, "-T", "fields"]
    for f in field_args:
        args += ["-e", f]
    proc = run_sync(args, timeout=settings.tshark_timeout_s)
    return tp.parse_field_lines(proc.stdout)


def apply_display_filter(path: Path, dfilter: str, offset: int = 0, limit: int | None = None) -> dict:
    """Wendet einen Display-Filter auf eine vorhandene PCAP an.
    Begrenzt auf max. ``display_filter_max_packets`` Pakete, mit Paging."""
    dfilter = validate_dfilter(dfilter)
    max_pkts = settings.display_filter_max_packets
    limit = max_pkts if limit is None else min(limit, max_pkts)

    args = [
        settings.tshark_bin,
        "-r",
        str(path),
        "-Y",
        dfilter,
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "frame.time_epoch",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "_ws.col.Protocol",
        "-e",
        "frame.len",
        "-e",
        "_ws.col.Info",
        "-E",
        "separator=\t",
    ]
    proc = run_sync(args, timeout=settings.tshark_timeout_s)
    rows = tp.parse_field_lines(proc.stdout)
    total = len(rows)
    page = rows[offset : offset + limit]
    packets = []
    for line in page:
        cols = line.split("\t")
        cols += [""] * (7 - len(cols))
        packets.append(
            {
                "number": cols[0],
                "time_epoch": cols[1],
                "src": cols[2],
                "dst": cols[3],
                "protocol": cols[4],
                "length": cols[5],
                "info": cols[6],
            }
        )
    return {"total": total, "offset": offset, "limit": limit, "packets": packets}
