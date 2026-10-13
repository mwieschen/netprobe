"""TCP-Retransmission-Monitoring: passive Kurz-Aufnahme (dumpcap) + Analyse
(tshark) + Klassifikation der wahrscheinlichen Ursache. Zusätzlich aktive Tests:
gezielte TCP-Connect-Proben (reines stdlib ``socket``, kein Payload) zu frei
definierbaren Zielen, parallel zu einem Mitschnitt, der auf diese Ziele gefiltert
ist (vgl. ``voip_service._CaptureThread`` – gleiches Muster für einen parallelen
Mitschnitt neben eigenen Probes).

Reine Parsing-/Aggregations-/Klassifikationsfunktionen stehen am Anfang der
Datei (ohne Subprozess-/DB-Zugriff, per Unit-Test abdeckbar). Die eigentliche
Test-Ausführung (``run_test``) nutzt ``capture_service`` für die Aufnahme und
``proc.run_sync`` für tshark – beides bereits mit Argumentlisten/Validierung.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.models.retransmission import RetransmissionTarget, RetransmissionTest, RetransmissionTimelinePoint
from app.services import audit, capture_service
from app.services.proc import ToolNotFoundError, ensure_tool, run_sync, validate_network_target

log = get_logger("netprobe.retransmission")

# Obergrenze für aktive Tests: begrenzt BPF-Länge, Thread-Anzahl und Testumfang.
MAX_ACTIVE_TARGETS = 10
# Nur TCP wird unterstützt: Retransmissions (tcp.analysis.retransmission) sind
# ein TCP-Konzept, UDP-Paketverlust wird bereits über iperf3-UDP-Tests abgedeckt.
SUPPORTED_ACTIVE_PROTOCOLS = ("tcp",)


# --------------------------------------------------------------------------- #
# Reine Funktionen (Parsing, Aggregation, Klassifikation)
# --------------------------------------------------------------------------- #


def parse_retransmission_lines(text: str) -> list[dict]:
    """Parst die Ausgabe von ``tshark -Y tcp -T fields`` mit den Feldern (in
    dieser Reihenfolge, Tab-getrennt)::

        frame.time_epoch  ip.src  ip.dst  tcp.srcport  tcp.dstport
        tcp.analysis.retransmission  frame.len
    """
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        cols = line.split("\t")
        cols += [""] * (7 - len(cols))
        t, src, dst, sport, dport, retrans, length = (c.strip() for c in cols[:7])
        try:
            t_f = float(t)
        except ValueError:
            continue
        if not src or not dst:
            continue
        try:
            length_i = int(length)
        except ValueError:
            length_i = 0
        rows.append(
            {
                "t": t_f,
                "src": src,
                "dst": dst,
                "sport": _to_port(sport),
                "dport": _to_port(dport),
                "retrans": retrans not in ("", "0"),
                "length": length_i,
            }
        )
    return rows


def _to_port(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _dominant_direction(counts: dict[str, int]) -> str:
    up, down = counts.get("upload", 0), counts.get("download", 0)
    if up and not down:
        return "upload"
    if down and not up:
        return "download"
    if up and down:
        return "both"
    return "unknown"


def _target_for_row(r: dict, local_ip: str | None) -> tuple[str, int | None, str]:
    if local_ip and r["src"] == local_ip:
        return r["dst"], r["dport"], "upload"
    if local_ip and r["dst"] == local_ip:
        return r["src"], r["sport"], "download"
    return r["dst"], r["dport"], "unknown"


def aggregate_retransmissions(
    rows: list[dict], local_ip: str | None, configured: list[tuple[str, int]] | None = None
) -> dict:
    """Aggregiert geparste TCP-Zeilen zu Gesamt-Kennzahlen und einer Liste
    betroffener Ziele (gruppiert nach der jeweils "fernen" Adresse).

    Ist ``local_ip`` bekannt, wird zusätzlich die Richtung bestimmt: Retransmits
    mit ``src == local_ip`` sind unsere ausgehenden Segmente (Upload), Retransmits
    mit ``dst == local_ip`` sind eingehende Segmente des Ziels (Download).

    Ohne ``configured`` (passiver Test) werden nur Ziele mit mindestens einer
    Retransmission aufgenommen (Auto-Discovery, sonst unbegrenzt viele Conversations).
    Mit ``configured`` (aktiver Test, Liste von (ip, port)) wird für **jedes**
    konfigurierte Ziel ein Eintrag geliefert – inklusive 0 Retransmissions/0 Paketen,
    falls gar kein Traffic erfasst wurde.
    """
    total_packets = len(rows)
    retrans_rows = [r for r in rows if r["retrans"]]
    total_retrans = len(retrans_rows)
    rate = round(total_retrans / total_packets * 100, 3) if total_packets else 0.0

    by_target: dict[tuple[str, int | None], dict] = {}

    def _entry(ip: str, port: int | None) -> dict:
        return by_target.setdefault(
            (ip, port),
            {
                "ip": ip, "port": port, "retransmissions": 0, "packets": 0,
                "direction_counts": {"upload": 0, "download": 0, "unknown": 0},
                "first_t": None, "last_t": None,
            },
        )

    def _touch(e: dict, t: float) -> None:
        e["first_t"] = t if e["first_t"] is None else min(e["first_t"], t)
        e["last_t"] = t if e["last_t"] is None else max(e["last_t"], t)

    if configured:
        configured_ips = {ip for ip, _port in configured}
        for r in rows:
            ip, port, direction = _target_for_row(r, local_ip)
            if ip not in configured_ips:
                continue
            e = _entry(ip, port)
            e["packets"] += 1
            e["direction_counts"][direction] += 1
            if r["retrans"]:
                e["retransmissions"] += 1
            _touch(e, r["t"])
        # Konfigurierte Ziele ohne jeglichen erfassten Traffic trotzdem aufnehmen
        # (0/0 macht einen vollständigen Verbindungsausfall sichtbar).
        seen_ips = {ip for ip, _port in by_target}
        for ip, port in configured:
            if ip not in seen_ips:
                _entry(ip, port)
    else:
        for r in retrans_rows:
            ip, port, direction = _target_for_row(r, local_ip)
            e = _entry(ip, port)
            e["retransmissions"] += 1
            e["direction_counts"][direction] += 1
            _touch(e, r["t"])
        # Pakete je IP (für eine Rate je Ziel) aus allen Zeilen, nicht nur Retransmits.
        packets_per_ip: Counter[str] = Counter()
        for r in rows:
            packets_per_ip[r["src"]] += 1
            packets_per_ip[r["dst"]] += 1
        for (ip, _port), e in by_target.items():
            e["packets"] = packets_per_ip.get(ip, 0) or e["retransmissions"]

    targets: list[dict] = []
    for (ip, _port), e in by_target.items():
        pkts = e["packets"]
        targets.append(
            {
                "ip": e["ip"],
                "port": e["port"],
                "is_private": _is_private(ip),
                "retransmissions": e["retransmissions"],
                "packets": pkts,
                "rate_percent": round(e["retransmissions"] / pkts * 100, 3) if pkts else 0.0,
                "direction_counts": e["direction_counts"],
                "direction": _dominant_direction(e["direction_counts"]),
                "first_seen_epoch": e["first_t"],
                "last_seen_epoch": e["last_t"],
            }
        )
    targets.sort(key=lambda t: t["retransmissions"], reverse=True)

    return {
        "total_packets": total_packets,
        "total_retransmissions": total_retrans,
        "retransmission_rate_percent": rate,
        "targets": targets,
    }


def compute_timeline(rows: list[dict], buckets: int = 60) -> list[dict]:
    """Bucketisiert die Pakete über die Zeit (Pakete/s, Retransmissions/s, Rate)."""
    if not rows:
        return []
    buckets = max(1, min(buckets, 500))
    times = [r["t"] for r in rows]
    t0, t1 = min(times), max(times)
    span = max(t1 - t0, 1e-6)
    interval = span / buckets

    pkt_counts = [0] * buckets
    retrans_counts = [0] * buckets
    for r in rows:
        idx = int((r["t"] - t0) / interval)
        idx = 0 if idx < 0 else min(idx, buckets - 1)
        pkt_counts[idx] += 1
        if r["retrans"]:
            retrans_counts[idx] += 1

    out: list[dict] = []
    for i in range(buckets):
        pkts, rtr = pkt_counts[i], retrans_counts[i]
        out.append(
            {
                "t_offset_s": round(i * interval, 3),
                "packets_per_s": round(pkts / interval, 2) if interval else 0.0,
                "retrans_per_s": round(rtr / interval, 2) if interval else 0.0,
                "rate_percent": round(rtr / pkts * 100, 3) if pkts else 0.0,
            }
        )
    return out


def classify(total_retrans: int, targets: list[dict]) -> tuple[str, str]:
    """Klassifiziert die wahrscheinliche Ursache anhand der betroffenen Ziele.

    ``targets`` wie von :func:`aggregate_retransmissions` geliefert (benötigt
    ``is_private``, ``retransmissions`` und ``direction_counts``).
    """
    affected = [t for t in targets if t["retransmissions"] > 0]
    if total_retrans == 0 or not affected:
        return "OK", "Keine auffälligen Retransmissions im Testfenster."

    private = [t for t in affected if t["is_private"]]
    public = [t for t in affected if not t["is_private"]]

    if private and not public:
        return (
            "LOCAL_NETWORK_ISSUE",
            "Nur Ziele im lokalen Netzwerk betroffen. Switch-Ports, Kabel/WLAN und "
            "Auslastung im LAN prüfen.",
        )

    if public and not private:
        if len(public) == 1:
            t = public[0]
            ref = f"{t['ip']}:{t['port']}" if t.get("port") else t["ip"]
            return (
                "REMOTE_SERVER_ISSUE",
                f"Nur ein entferntes Ziel betroffen ({ref}). Serverseitig bzw. beim Betreiber "
                "dieses Ziels nachfragen.",
            )
        up = sum(t["direction_counts"].get("upload", 0) for t in public)
        down = sum(t["direction_counts"].get("download", 0) for t in public)
        if up and not down:
            return (
                "UPLINK_CONGESTION",
                "Retransmissions nur in Upload-Richtung über mehrere WAN-Ziele. Deutet auf eine "
                "Überlastung bzw. ein Problem der Upload-Bandbreite hin.",
            )
        if down and not up:
            return (
                "DOWNLINK_CONGESTION",
                "Retransmissions nur in Download-Richtung über mehrere WAN-Ziele. Deutet auf eine "
                "Überlastung bzw. ein Problem der Download-Bandbreite hin.",
            )
        return (
            "ISP_OR_WAN_LINK_ISSUE",
            "Mehrere unabhängige WAN-Ziele betroffen. Deutet auf ein Problem der "
            "Internetanbindung (Provider/Leitung) hin – Providertest/-Nachweis empfohlen.",
        )

    return (
        "UNKNOWN",
        "Sowohl lokale als auch externe Ziele betroffen – uneindeutiges Muster. Zunächst das "
        "lokale Netzwerk prüfen, danach erneut testen.",
    )


# --------------------------------------------------------------------------- #
# Aktive Tests: Zielauflösung + TCP-Connect-Proben (reines stdlib, kein Payload)
# --------------------------------------------------------------------------- #


@dataclass
class _ConnStats:
    attempts: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)


def resolve_active_target(host: str, port: int, protocol: str = "tcp") -> tuple[str, int]:
    """Validiert Host/Port eines aktiven Ziels und löst einen Hostnamen einmalig
    zu einer IPv4-Adresse auf (dieselbe Adresse wird dann sowohl für die eigenen
    Connect-Proben als auch für den BPF-Filter des Mitschnitts verwendet – so
    bleiben Traffic-Erzeugung und Aufnahme garantiert auf demselben Ziel, auch
    bei DNS-Round-Robin). Wirft ``ValueError`` bei ungültiger Eingabe."""
    if protocol not in SUPPORTED_ACTIVE_PROTOCOLS:
        raise ValueError(f"Nicht unterstütztes Protokoll für aktive Tests: {protocol!r} (nur TCP).")
    host = validate_network_target(host)
    if not (1 <= port <= 65535):
        raise ValueError(f"Ungültiger Port: {port}")
    try:
        ip = socket.gethostbyname(host)
    except OSError as exc:
        raise ValueError(f"Host nicht auflösbar: {host} ({exc})") from exc
    return ip, port


def _tcp_connect_probe(ip: str, port: int, timeout: float = 3.0) -> tuple[bool, float | None]:
    """Einzelne TCP-Connect-Probe ohne Payload (nur Handshake + sofortiges Schließen)."""
    start = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
        return True, (time.monotonic() - start) * 1000
    except OSError:
        return False, None


def _probe_loop(ip: str, port: int, duration_s: float, interval_s: float = 0.5) -> _ConnStats:
    """Wiederholt TCP-Connect-Proben zu einem Ziel für ``duration_s`` Sekunden."""
    stats = _ConnStats()
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        ok, latency_ms = _tcp_connect_probe(ip, port)
        stats.attempts += 1
        if ok and latency_ms is not None:
            stats.latencies_ms.append(latency_ms)
        else:
            stats.errors += 1
        time.sleep(interval_s)
    return stats


def _run_active_traffic(
    targets: list[tuple[str, int]], duration_s: float, concurrent: bool
) -> dict[tuple[str, int], _ConnStats]:
    """Führt die Connect-Probe-Schleife für jedes Ziel aus – parallel (gleichzeitige
    Last auf alle Ziele) oder nacheinander (isolierte Zuordnung, ``duration_s`` wird
    dafür gleichmäßig auf die Ziele aufgeteilt)."""
    if concurrent:
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {pool.submit(_probe_loop, ip, port, duration_s): (ip, port) for ip, port in targets}
            return {key: fut.result() for fut, key in futures.items()}

    per_target = max(2.0, duration_s / len(targets))
    return {(ip, port): _probe_loop(ip, port, per_target) for ip, port in targets}


class _CaptureThread(threading.Thread):
    """Führt den Mitschnitt parallel zur aktiven Traffic-Erzeugung aus (gleiches
    Muster wie ``voip_service._CaptureThread``)."""

    def __init__(self, iface: str, out_path: Path, duration_s: int, bpf: str | None) -> None:
        super().__init__(daemon=True)
        self._args = (iface, out_path, duration_s, bpf)
        self.result: Path | None = None

    def run(self) -> None:
        self.result = capture_service.run_capture_window(*self._args)


# --------------------------------------------------------------------------- #
# IO: Aufnahme, Analyse, Persistierung
# --------------------------------------------------------------------------- #


def _local_ipv4(iface: str) -> str | None:
    """Ermittelt die IPv4-Adresse des gegebenen Interfaces (``ip -4 addr show``),
    um Retransmissions als Upload/Download zuordnen zu können. Best effort –
    liefert ``None``, wenn das Tool fehlt oder keine Adresse gefunden wird."""
    try:
        proc = run_sync([settings.ip_bin, "-4", "addr", "show", "dev", iface], timeout=10)
    except ToolNotFoundError:
        return None
    m = re.search(r"inet (\d{1,3}(?:\.\d{1,3}){3})", proc.stdout)
    return m.group(1) if m else None


def _read_tcp_rows(path: Path) -> list[dict]:
    args = [
        settings.tshark_bin,
        "-r",
        str(path),
        "-Y",
        "tcp",
        "-T",
        "fields",
        "-e",
        "frame.time_epoch",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "tcp.srcport",
        "-e",
        "tcp.dstport",
        "-e",
        "tcp.analysis.retransmission",
        "-e",
        "frame.len",
        "-E",
        "separator=\t",
    ]
    proc = run_sync(args, timeout=settings.tshark_timeout_s)
    return parse_retransmission_lines(proc.stdout)


def run_test(
    db: Session,
    *,
    interface: str,
    duration_s: int,
    bpf: str | None = None,
    source: str = "manual",
    mode: str = "passive",
    targets: list[dict] | None = None,
    concurrent: bool = True,
) -> RetransmissionTest:
    """Führt einen Retransmission-Test aus: kurzer Mitschnitt auf ``interface``,
    tshark-Analyse, Klassifikation, Persistierung. Die PCAP-Datei wird danach
    gelöscht (nur die Auswertung bleibt erhalten).

    - ``mode="passive"`` (Standard): wertet laufenden Traffic aus, Ziele werden
      aus den beobachteten Retransmissions automatisch ermittelt.
    - ``mode="active"``: ``targets`` (Liste von ``{"host", "port", "protocol"}``,
      max. ``MAX_ACTIVE_TARGETS``) werden per TCP-Connect-Probe aktiv angetestet,
      während parallel ein auf diese Ziele gefilterter Mitschnitt läuft.
    """
    iface = capture_service.validate_iface(interface)
    duration_s = max(5, min(duration_s, settings.retrans_max_duration_s))
    mode = mode if mode in ("passive", "active") else "passive"

    resolved: list[tuple[str, int]] = []
    target_meta: list[dict] = []
    if mode == "active":
        if not targets:
            raise ValueError("Aktiver Test benötigt mindestens ein Ziel.")
        if len(targets) > MAX_ACTIVE_TARGETS:
            raise ValueError(f"Höchstens {MAX_ACTIVE_TARGETS} Ziele je aktivem Test.")
        for t in targets:
            protocol = (t.get("protocol") or "tcp").lower()
            ip, port = resolve_active_target(t["host"], int(t["port"]), protocol)
            resolved.append((ip, port))
            target_meta.append({"host": t["host"], "ip": ip, "port": port})
        bpf_val = capture_service.validate_bpf(
            "tcp and (" + " or ".join(f"host {ip}" for ip, _port in resolved) + ")"
        )
    else:
        bpf_val = capture_service.validate_bpf(bpf) if bpf else "tcp"

    test = RetransmissionTest(
        source=source, interface=iface, bpf=bpf_val, duration_s=duration_s, status="running",
        test_mode=mode, concurrent=concurrent,
        target_ips=json.dumps(target_meta) if target_meta else None,
    )
    db.add(test)
    db.commit()
    db.refresh(test)

    out_dir = settings.capture_dir / "retransmission"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"retrans_{test.id}_{int(utcnow().timestamp())}.pcapng"

    conn_stats: dict[tuple[str, int], _ConnStats] = {}
    if mode == "active":
        try:
            ensure_tool(settings.dumpcap_bin)
        except ToolNotFoundError as exc:
            # Fail fast statt die volle Testdauer mit Connect-Proben zu verbringen,
            # obwohl der parallele Mitschnitt ohnehin nicht starten kann.
            test.status = "error"
            test.error = str(exc)
            db.commit()
            db.refresh(test)
            return test
        cap_thread = _CaptureThread(iface, out_path, duration_s, bpf_val)
        cap_thread.start()
        conn_stats = _run_active_traffic(resolved, float(duration_s), concurrent)
        cap_thread.join()
        pcap = cap_thread.result
    else:
        pcap = capture_service.run_capture_window(iface, out_path, duration_s, bpf=bpf_val)

    if pcap is None:
        test.status = "error"
        test.error = "Aufnahme fehlgeschlagen (siehe Log)."
        db.commit()
        db.refresh(test)
        return test

    try:
        rows = _read_tcp_rows(pcap)
    except ToolNotFoundError as exc:
        test.status = "error"
        test.error = str(exc)
        db.commit()
        db.refresh(test)
        return test
    finally:
        pcap.unlink(missing_ok=True)

    local_ip = _local_ipv4(iface)
    agg = aggregate_retransmissions(rows, local_ip, configured=resolved or None)
    classification, recommendation = classify(agg["total_retransmissions"], agg["targets"])
    timeline = compute_timeline(rows)

    test.total_packets = agg["total_packets"]
    test.tcp_packets = agg["total_packets"]
    test.total_retransmissions = agg["total_retransmissions"]
    test.retransmission_rate_percent = agg["retransmission_rate_percent"]
    test.classification = classification
    test.recommendation = recommendation
    test.status = "done"

    for t in agg["targets"]:
        cstats = conn_stats.get((t["ip"], t["port"]))
        db.add(
            RetransmissionTarget(
                test_id=test.id,
                target_ip=t["ip"],
                target_port=t["port"],
                is_private=t["is_private"],
                retransmissions=t["retransmissions"],
                packets=t["packets"],
                rate_percent=t["rate_percent"],
                direction=t["direction"],
                first_seen=(
                    datetime.fromtimestamp(t["first_seen_epoch"], tz=UTC)
                    if t["first_seen_epoch"] is not None
                    else None
                ),
                last_seen=(
                    datetime.fromtimestamp(t["last_seen_epoch"], tz=UTC)
                    if t["last_seen_epoch"] is not None
                    else None
                ),
                connection_attempts=cstats.attempts if cstats else None,
                connection_errors=cstats.errors if cstats else None,
                avg_connect_latency_ms=(
                    round(sum(cstats.latencies_ms) / len(cstats.latencies_ms), 2)
                    if cstats and cstats.latencies_ms
                    else None
                ),
            )
        )
    for p in timeline:
        db.add(
            RetransmissionTimelinePoint(
                test_id=test.id,
                t_offset_s=p["t_offset_s"],
                retrans_per_s=p["retrans_per_s"],
                packets_per_s=p["packets_per_s"],
                rate_percent=p["rate_percent"],
            )
        )

    db.commit()
    db.refresh(test)

    _check_thresholds(db, test)
    log.info(
        "Retransmission-Test %s fertig: %s Pakete, %s Retransmissions (%.2f %%), %s",
        test.id,
        test.total_packets,
        test.total_retransmissions,
        test.retransmission_rate_percent or 0.0,
        test.classification,
    )
    return test


def _check_thresholds(db: Session, test: RetransmissionTest) -> None:
    """Schreibt bei Schwellwert-Überschreitung einen Log-/Audit-Eintrag (lokal,
    keine ausgehende Benachrichtigung – netprobe bleibt cloud-/telemetriefrei)."""
    rate = test.retransmission_rate_percent or 0.0
    if rate >= settings.retrans_alert_threshold_pct:
        action, label, threshold = "retransmission_alert", "KRITISCH", settings.retrans_alert_threshold_pct
    elif rate >= settings.retrans_warn_threshold_pct:
        action, label, threshold = "retransmission_warn", "WARNUNG", settings.retrans_warn_threshold_pct
    else:
        return
    msg = (
        f"{label}: TCP-Retransmission-Rate {rate:.2f} % auf {test.interface} "
        f"(Test #{test.id}, Schwelle {threshold:.2f} %)."
    )
    log.warning(msg)
    audit.record(db, action, detail=msg)
    db.commit()
