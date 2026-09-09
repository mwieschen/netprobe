"""Traceroute / Pfad-Analyse.

Primäre Engine ist ``mtr --json`` (per-Hop Loss/Latenz/StdAbw in einem Lauf).
Fehlt ``mtr``, wird auf ``traceroute`` (Textausgabe) zurückgefallen. Beide laufen
mit dem ``CAP_NET_RAW`` des Dienstes ohne Root.

Anschließend werden die Hops angereichert:
- Reverse-DNS (``socket.gethostbyaddr``, best effort, kurzes Timeout),
- Geo/ASN über :mod:`app.services.geoip_service` (Hybrid MaxMind → ip-api.com),
- optional Pfad-MTU zum letzten erreichten Hop über
  :func:`app.services.mtu_service.discover_path_mtu`.

Sicherheit: Argumentliste, ``--``-Trenner, Ziel über
``proc.validate_network_target``; keine Shell.
"""

from __future__ import annotations

import json
import re
import shutil
import socket

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.traceroute import Traceroute, TracerouteHop
from app.services import geoip_service, mtu_service
from app.services.proc import ProcResult, ToolNotFoundError, run_sync, validate_network_target

log = get_logger("netprobe.traceroute")

_TR_HOP_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
_TR_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{2,})")
_TR_MS_RE = re.compile(r"([\d.]+)\s*ms")

_VALID_MODES = ("icmp", "udp", "tcp")


# --------------------------------------------------------------------------- #
# Kommandozeilen
# --------------------------------------------------------------------------- #

def _build_mtr_args(host: str, mode: str, tcp_port: int, max_hops: int, queries: int) -> list[str]:
    args = [
        settings.mtr_bin, "--json", "--no-dns",
        "-c", str(queries), "-m", str(max_hops),
    ]
    if mode == "udp":
        args.append("-u")
    elif mode == "tcp":
        args += ["-T", "-P", str(tcp_port)]
    args += ["--", host]
    return args


def _build_traceroute_args(host: str, mode: str, tcp_port: int, max_hops: int, queries: int) -> list[str]:
    args = [settings.traceroute_bin, "-n", "-q", str(queries), "-m", str(max_hops)]
    if mode == "udp":
        args.append("-U")
    elif mode == "tcp":
        args += ["-T", "-p", str(tcp_port)]
    else:
        args.append("-I")
    args += ["--", host]
    return args


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def parse_mtr_json(stdout: str) -> list[dict]:
    """Parst ``mtr --json`` in eine Hop-Liste (leere Liste bei Fehler)."""
    try:
        data = json.loads(stdout)
        hubs = data["report"]["hubs"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    hops: list[dict] = []
    for hub in hubs:
        raw_ip = str(hub.get("host") or "").strip()
        ip = None if raw_ip in ("", "???", "waiting for reply") else raw_ip
        loss = hub.get("Loss%")
        avg = hub.get("Avg")
        hops.append({
            "hop_number": int(hub.get("count", len(hops) + 1)),
            "ip": ip,
            "loss_pct": round(float(loss), 2) if loss is not None else None,
            "sent": int(hub["Snt"]) if hub.get("Snt") is not None else None,
            "latency_min_ms": _f(hub.get("Best")),
            "latency_avg_ms": _f(avg),
            "latency_max_ms": _f(hub.get("Wrst")),
        })
    return hops


def parse_traceroute_text(stdout: str) -> list[dict]:
    """Parst die klassische ``traceroute -n``-Textausgabe."""
    hops: list[dict] = []
    for line in stdout.splitlines():
        m = _TR_HOP_RE.match(line)
        if not m:
            continue
        hop_no = int(m.group(1))
        rest = m.group(2)
        ips = _TR_IP_RE.findall(rest)
        times = [float(x) for x in _TR_MS_RE.findall(rest)]
        star_count = rest.count("*")
        total = len(times) + star_count or 1
        loss = round(star_count / total * 100, 2) if total else None
        hops.append({
            "hop_number": hop_no,
            "ip": ips[0] if ips else None,
            "loss_pct": loss,
            "sent": total,
            "latency_min_ms": min(times) if times else None,
            "latency_avg_ms": round(sum(times) / len(times), 3) if times else None,
            "latency_max_ms": max(times) if times else None,
        })
    return hops


def _f(v) -> float | None:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Ausführung
# --------------------------------------------------------------------------- #

def _run_engine(
    host: str, mode: str, tcp_port: int, max_hops: int, queries: int
) -> tuple[str, list[dict], str | None]:
    """Führt mtr (bevorzugt) oder traceroute aus. Rückgabe ``(engine, hops, error)``."""
    if shutil.which(settings.mtr_bin):
        try:
            proc: ProcResult = run_sync(
                _build_mtr_args(host, mode, tcp_port, max_hops, queries),
                timeout=settings.traceroute_timeout_s,
            )
        except ToolNotFoundError as exc:
            return "mtr", [], str(exc)
        hops = parse_mtr_json(proc.stdout)
        if hops:
            return "mtr", hops, None
        err = proc.stderr.strip() or f"mtr Exit-Code {proc.returncode}"
        # kein Fallback, wenn mtr existiert aber scheitert (meist Rechte/Ziel)
        return "mtr", [], err

    if shutil.which(settings.traceroute_bin):
        try:
            proc = run_sync(
                _build_traceroute_args(host, mode, tcp_port, max_hops, queries),
                timeout=settings.traceroute_timeout_s,
            )
        except ToolNotFoundError as exc:
            return "traceroute", [], str(exc)
        hops = parse_traceroute_text(proc.stdout)
        if hops:
            return "traceroute", hops, None
        return "traceroute", [], proc.stderr.strip() or f"traceroute Exit-Code {proc.returncode}"

    return "mtr", [], "Weder 'mtr' noch 'traceroute' ist installiert."


def _reverse_dns(ip: str) -> str | None:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2.0)
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return None
    finally:
        socket.setdefaulttimeout(old)


def run(
    db: Session,
    *,
    host: str,
    mode: str = "icmp",
    tcp_port: int = 443,
    queries: int = 3,
    max_hops: int = 30,
    resolve_geo: bool = True,
    measure_mtu: bool = False,
    source: str = "manual",
    target_id: int | None = None,
) -> Traceroute:
    """Führt einen Traceroute aus, reichert ihn an und persistiert ihn."""
    mode = mode if mode in _VALID_MODES else "icmp"
    max_hops = max(1, min(int(max_hops), settings.traceroute_max_hops_limit))
    queries = max(1, min(int(queries), 10))

    try:
        host = validate_network_target(host)
    except ValueError as exc:
        tr = Traceroute(target=host, target_id=target_id, source=source, mode=mode,
                        completed=False, error=str(exc))
        db.add(tr)
        db.commit()
        db.refresh(tr)
        return tr

    engine, raw_hops, error = _run_engine(host, mode, tcp_port, max_hops, queries)

    tr = Traceroute(
        target=host, target_id=target_id, source=source, mode=mode, engine=engine,
        total_hops=len(raw_hops), completed=bool(raw_hops), error=error,
    )

    # Reverse-DNS + Geo
    unique_ips = sorted({h["ip"] for h in raw_hops if h["ip"]})
    geo: dict[str, dict] = {}
    if resolve_geo and unique_ips:
        try:
            geo = geoip_service.lookup(db, unique_ips)
        except Exception as exc:  # noqa: BLE001 - Geo ist optional
            log.warning("Geo-Auflösung fehlgeschlagen: %s", exc)

    dest_reached = False
    lat_values: list[float] = []
    for h in raw_hops:
        ip = h["ip"]
        g = geo.get(ip or "", {})
        hop = TracerouteHop(
            hop_number=h["hop_number"],
            ip=ip,
            hostname=_reverse_dns(ip) if ip else None,
            latency_min_ms=h["latency_min_ms"],
            latency_avg_ms=h["latency_avg_ms"],
            latency_max_ms=h["latency_max_ms"],
            loss_pct=h["loss_pct"],
            sent=h["sent"],
            country=g.get("country"),
            city=g.get("city"),
            asn=g.get("asn"),
            isp=g.get("isp"),
            lat=g.get("lat"),
            lon=g.get("lon"),
        )
        tr.hops.append(hop)
        if h["latency_avg_ms"] is not None:
            lat_values.append(h["latency_avg_ms"])

    if raw_hops:
        last = raw_hops[-1]
        dest_reached = last["ip"] is not None and (last["loss_pct"] or 0) < 100
        tr.dest_reached = dest_reached
        tr.avg_latency_ms = round(lat_values[-1], 3) if lat_values else None
        tr.total_loss_pct = last["loss_pct"]

    # optionale Pfad-MTU zum letzten erreichten Hop
    if measure_mtu and dest_reached and tr.hops:
        target_ip = tr.hops[-1].ip
        if target_ip:
            try:
                res = mtu_service.discover_path_mtu(target_ip, 576, 1500)
                if res.get("ok"):
                    tr.hops[-1].mtu = res["path_mtu"]
            except Exception as exc:  # noqa: BLE001
                log.warning("MTU-Messung zu %s fehlgeschlagen: %s", target_ip, exc)

    db.add(tr)
    db.commit()
    db.refresh(tr)
    log.info("Traceroute zu %s: %d Hops (engine=%s, dest_reached=%s)",
             host, len(raw_hops), engine, dest_reached)
    return tr


# --------------------------------------------------------------------------- #
# Vergleich zweier Läufe
# --------------------------------------------------------------------------- #

def compare(a: Traceroute, b: Traceroute) -> dict:
    """Stellt zwei Läufe hop-weise gegenüber."""
    by_a = {h.hop_number: h for h in a.hops}
    by_b = {h.hop_number: h for h in b.hops}
    rows = []
    for n in sorted(set(by_a) | set(by_b)):
        ha, hb = by_a.get(n), by_b.get(n)
        ip_a = ha.ip if ha else None
        ip_b = hb.ip if hb else None
        lat_a = ha.latency_avg_ms if ha else None
        lat_b = hb.latency_avg_ms if hb else None
        delta = round(lat_b - lat_a, 3) if lat_a is not None and lat_b is not None else None
        rows.append({
            "hop_number": n,
            "ip_a": ip_a, "ip_b": ip_b,
            "hostname_a": ha.hostname if ha else None,
            "hostname_b": hb.hostname if hb else None,
            "latency_avg_a": lat_a, "latency_avg_b": lat_b,
            "delta_ms": delta,
            "changed": ip_a != ip_b,
        })
    return {
        "a": {"id": a.id, "ts": a.ts.isoformat(), "target": a.target},
        "b": {"id": b.id, "ts": b.ts.isoformat(), "target": b.target},
        "rows": rows,
        "path_changed": any(r["changed"] for r in rows),
    }
