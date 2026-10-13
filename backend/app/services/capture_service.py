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


_SYS_NET = Path("/sys/class/net")
# "unknown" melden manche virtuellen/Tunnel-Interfaces, obwohl sie Traffic führen.
_USABLE_OPERSTATES = {"up", "unknown"}


def _iface_usable(iface: str) -> bool:
    """True, wenn das Interface existiert und nicht down ist. Ohne ``/sys/class/net``
    (z. B. Entwicklungsrechner) wird nichts verworfen."""
    if not _SYS_NET.is_dir():
        return True
    try:
        state = (_SYS_NET / iface / "operstate").read_text().strip().lower()
    except OSError:
        return False
    return state in _USABLE_OPERSTATES


def default_route_interface() -> str | None:
    """Interface der Default-Route (``ip route show default``), IPv4 vor IPv6.
    Best effort – ``None``, wenn ``ip`` fehlt oder keine Default-Route existiert."""
    for family in ("-4", "-6"):
        try:
            proc = run_sync([settings.ip_bin, family, "route", "show", "default"], timeout=10)
        except (ToolNotFoundError, OSError):
            return None
        for line in proc.stdout.splitlines():
            m = re.search(r"\bdev\s+(\S+)", line)
            if m and _IFACE_RE.match(m.group(1)):
                return m.group(1)
    return None


def resolve_default_interface(configured: str | None = None) -> str:
    """Ermittelt das Standard-Capture-Interface.

    ``configured`` (Standard: ``settings.retrans_default_interface``) wird verwendet,
    wenn es explizit gesetzt ist, existiert und nicht down ist. Bei ``auto``/leer oder
    einem fehlenden/inaktiven Interface (z. B. das frühere Default ``eth0`` auf Hosts
    mit ``ens18``/``enp0s3``) wird das Interface der Default-Route genommen."""
    cfg = (configured if configured is not None else settings.retrans_default_interface).strip()
    explicit = bool(cfg) and cfg.lower() != "auto"
    if explicit and _IFACE_RE.match(cfg) and _iface_usable(cfg):
        return cfg
    detected = default_route_interface()
    if detected:
        if explicit:
            log.warning("Interface %r nicht vorhanden oder inaktiv – verwende %r (Default-Route)",
                        cfg, detected)
        return detected
    # Letzter Ausweg: konfigurierten Wert behalten bzw. dumpcap-Pseudo-Interface "any".
    return cfg if explicit else "any"


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


def run_capture_window(
    iface: str, out_path: Path, duration_s: int, bpf: str | None = None
) -> Path | None:
    """Blockierender Kurz-Mitschnitt (für den parallelen VoIP-Test-Mitschnitt).

    Validiert Interface/BPF, ruft ``dumpcap`` mit ``-a duration:`` und gibt die
    geschriebene Datei zurück oder ``None``. Kein DB-Zugriff. Fehler werden nur
    geloggt (der Aufrufer läuft weiter).
    """
    duration_s = max(1, min(duration_s, settings.capture_max_duration_s))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        args = build_dumpcap_args(
            validate_iface(iface), out_path, duration_s,
            max_packets=None, max_filesize_mb=None, ringbuffer=None,
            bpf=validate_bpf(bpf),
        )
        proc = run_sync(args, timeout=duration_s + 20)
    except (CaptureValidationError, ToolNotFoundError) as exc:
        log.warning("VoIP-Mitschnitt fehlgeschlagen: %s", exc)
        return None
    if not out_path.exists():
        log.warning("VoIP-Mitschnitt erzeugte keine Datei: %s", proc.stderr.strip())
        return None
    return out_path


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


_METRIC_FIELDS = (
    "eth.dst.ig",
    "eth.dst",
    "frame.len",
    "_ws.col.Protocol",
    "ip.src",
    "ip.dst",
    "tcp.flags",
)


def _true_packet_count(path: Path) -> int | None:
    """Gesamtzahl Pakete über ``capinfos`` (liest nur Header/Index, sehr schnell)."""
    try:
        proc = run_sync([settings.capinfos_bin, "-c", "-M", str(path)], timeout=30)
    except ToolNotFoundError:
        return None
    m = re.search(r"Number of packets:\s*(\d+)", proc.stdout)
    return int(m.group(1)) if m else None


def analyze(path: Path, max_packets: int | None = None) -> dict:
    """Sichere Grundanalyse einer PCAP-Datei über tshark.

    Es werden höchstens ``max_packets`` Pakete vom Dateianfang ausgewertet
    (Standard: ``settings.analysis_max_packets``). Für größere Captures ist
    ``sampled`` gesetzt und ``packet_count`` weist die tatsächliche Gesamtzahl aus.
    """
    if not path.exists():
        raise FileNotFoundError(str(path))

    cap = max(1000, int(max_packets) if max_packets else settings.analysis_max_packets)
    base = [settings.tshark_bin, "-r", str(path), "-c", str(cap)]
    top_n = settings.analysis_top_n

    def _z(*z_args: str) -> str:
        proc = run_sync([*base, "-q", *z_args], timeout=settings.tshark_timeout_s)
        return proc.stdout

    def _fields_filtered(dfilter: str, *field_args: str) -> list[str]:
        args = [*base, "-Y", dfilter, "-T", "fields"]
        for f in field_args:
            args += ["-e", f]
        proc = run_sync(args, timeout=settings.tshark_timeout_s)
        return tp.parse_field_lines(proc.stdout)

    # Ein Durchlauf für Verteilung, Top-Protokolle, Top-Conversations, TCP-Anzahl.
    metric_args = [*base, "-T", "fields", "-E", "occurrence=f", "-E", "separator=\t"]
    for f in _METRIC_FIELDS:
        metric_args += ["-e", f]
    metrics = tp.parse_capture_metrics(
        run_sync(metric_args, timeout=settings.tshark_timeout_s).stdout, top_n=top_n
    )

    protocol_hierarchy = tp.parse_protocol_hierarchy(_z("-z", "io,phs"))
    endpoints = tp.parse_endpoints(_z("-z", "endpoints,ip"))
    tcp_conv = tp.parse_conversations(_z("-z", "conv,tcp"))
    udp_conv = tp.parse_conversations(_z("-z", "conv,udp"))
    expert = tp.parse_expert_info(_z("-z", "expert"))

    dns = tp.aggregate_dns_queries(_fields_filtered("dns.flags.response == 0", "dns.qry.name"))
    tls_sni = tp.aggregate_tls_sni(
        _fields_filtered(
            "tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name"
        )
    )
    retrans = len(_fields_filtered("tcp.analysis.retransmission", "frame.number"))
    dup_acks = len(_fields_filtered("tcp.analysis.duplicate_ack", "frame.number"))
    resets = len(_fields_filtered("tcp.flags.reset == 1", "frame.number"))

    tcp_packets = metrics["tcp_packets"]
    tcp_health = {
        "tcp_packets": tcp_packets,
        "retransmissions": retrans,
        "retransmission_pct": round(retrans / tcp_packets * 100, 2) if tcp_packets else 0.0,
        "dup_acks": dup_acks,
        "dup_ack_pct": round(dup_acks / tcp_packets * 100, 2) if tcp_packets else 0.0,
        "resets": resets,
    }

    analyzed = metrics["packets_analyzed"]
    true_total = _true_packet_count(path)
    packet_count = true_total if true_total is not None else analyzed
    sampled = analyzed >= cap and packet_count > analyzed

    distribution = metrics["frame_distribution"]
    warnings = _analysis_warnings(distribution, tcp_health)

    return {
        "packet_count": packet_count,
        "packets_analyzed": analyzed,
        "sampled": sampled,
        "frame_distribution": distribution,
        "top_protocols": metrics["top_protocols"],
        "top_conversations": metrics["top_conversations"],
        "tcp_health": tcp_health,
        "warnings": warnings,
        # bestehende Felder (Kompatibilität)
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


def _analysis_warnings(distribution: dict, tcp_health: dict) -> list[dict]:
    """Erzeugt Warnungen anhand der zentral in ``settings`` definierten Schwellwerte."""
    warnings: list[dict] = []
    bpct = distribution["broadcast_pct"]
    mpct = distribution["multicast_pct"]
    if bpct > settings.analysis_broadcast_warn_pct:
        warnings.append({
            "level": "warn",
            "code": "high_broadcast",
            "message": f"Hoher Broadcast-Anteil: {bpct} % (Schwelle "
                       f"{settings.analysis_broadcast_warn_pct} %).",
        })
    if mpct > settings.analysis_multicast_warn_pct:
        warnings.append({
            "level": "warn",
            "code": "high_multicast",
            "message": f"Hoher Multicast-Anteil: {mpct} % (Schwelle "
                       f"{settings.analysis_multicast_warn_pct} %).",
        })
    rpct = tcp_health["retransmission_pct"]
    if tcp_health["tcp_packets"] >= 100 and rpct > settings.analysis_tcp_retransmission_warn_pct:
        warnings.append({
            "level": "warn",
            "code": "high_tcp_retransmission",
            "message": f"Hohe TCP-Retransmission-Rate: {rpct} % (Schwelle "
                       f"{settings.analysis_tcp_retransmission_warn_pct} %).",
        })
    if tcp_health["resets"] > settings.analysis_tcp_reset_warn:
        warnings.append({
            "level": "info",
            "code": "many_tcp_resets",
            "message": f"Viele TCP-RST-Pakete: {tcp_health['resets']} "
                       f"(Schwelle {settings.analysis_tcp_reset_warn}).",
        })
    return warnings


def conversations_ip(path: Path) -> list[dict]:
    """IP-Conversations einer PCAP (``tshark -q -z conv,ip``)."""
    proc = run_sync(
        [settings.tshark_bin, "-r", str(path), "-q", "-z", "conv,ip"],
        timeout=settings.tshark_timeout_s,
    )
    return tp.parse_conversations(proc.stdout)


def capture_stats(path: Path, buckets: int = 120) -> dict:
    """Einfache I/O-Statistik über die Zeit (Pakete/s, Bit/s) plus Gesamtwerte."""
    proc = run_sync(
        [
            settings.tshark_bin,
            "-r",
            str(path),
            "-T",
            "fields",
            "-e",
            "frame.time_epoch",
            "-e",
            "frame.len",
        ],
        timeout=settings.tshark_timeout_s,
    )
    rows = tp.parse_epoch_len_lines(proc.stdout)
    return tp.compute_io_buckets(rows, buckets)


def apply_display_filter(path: Path, dfilter: str = "", offset: int = 0, limit: int | None = None) -> dict:
    """Wendet einen Display-Filter auf eine vorhandene PCAP an und liefert eine
    Paketliste. Ein leerer Filter listet alle Pakete. Begrenzt auf max.
    ``display_filter_max_packets`` Pakete, mit Paging."""
    dfilter = (dfilter or "").strip()
    max_pkts = settings.display_filter_max_packets
    limit = max_pkts if limit is None else min(limit, max_pkts)
    offset = max(0, offset)

    args = [settings.tshark_bin, "-r", str(path)]
    if dfilter:
        args += ["-Y", validate_dfilter(dfilter)]
    args += [
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
