"""Network Diagnostics: orchestriert bestehende Tests (Ping/WAN, aktive
TCP-Retransmissions, MTU, Traceroute, Speedtest, Broadcast-Anteil) parallel zu
mehreren Zielen und fasst sie zu einer Ampel-Übersicht (OK/WARN/KRITISCH) zusammen.

Reine Orchestrierung: jeder Sub-Test ruft den bestehenden, längst eigenständig
getesteten Service auf (``wan_service.measure``, ``mtu_service.discover_path_mtu``,
``traceroute_service.run``, ``speedtest_service.run_speedtest``,
``retransmission_service.run_test`` im aktiven Modus, ``capture_service.analyze``
für den Broadcast-Anteil) – keine Duplikation von Test-Logik. Mehrere Ziele im
aktiven Retransmission-Test werden in EINEM gemeinsamen Mitschnitt getestet
(unterstützt bereits mehrere Ziele nativ), statt pro Ziel einen eigenen
Mitschnitt zu starten.

``classify_target``/``classify_run`` sind reine Funktionen (ohne IO), unit-testbar,
und nutzen bewusst dieselben Schwellwerte wie die jeweiligen Einzel-Features
(``report_*``, ``retrans_*``, ``analysis_broadcast_warn_pct``) statt einer
zweiten, potenziell abweichenden Schwellwert-Quelle.
"""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import to_local, utcnow
from app.db.session import session_scope
from app.models.diagnostics import DiagnosticRun
from app.models.mtu import MtuTest
from app.services import (
    capture_service,
    mtu_service,
    retransmission_service,
    speedtest_service,
    traceroute_service,
    wan_service,
)

log = get_logger("netprobe.diagnostics")

TEST_TYPES = ("ping", "retrans", "mtu", "broadcast", "traceroute", "speedtest")
# Diagnose-Ziele haben (anders als die dedizierte Retransmission-Seite) keinen
# Port; für den aktiven TCP-Test wird der am häufigsten offene Port verwendet.
_DEFAULT_RETRANS_PORT = 443


class DiagnosticsError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Reine Klassifikation
# --------------------------------------------------------------------------- #

def classify_target(ping: dict | None, retrans: dict | None, mtu: dict | None) -> tuple[str, list[str]]:
    """Bestimmt Ampel-Status + Problemliste für ein einzelnes Ziel."""
    issues: list[str] = []

    if ping and ping.get("error") is None:
        if (ping.get("loss_pct") or 0) > settings.report_ping_loss_warn_pct:
            issues.append("packet_loss_high")
        if ping.get("jitter_ms") is not None and ping["jitter_ms"] > settings.report_jitter_warn_ms:
            issues.append("jitter_high")
        if ping.get("rtt_avg") is not None and ping["rtt_avg"] > settings.diagnostics_ping_latency_warn_ms:
            issues.append("latency_high")
    elif ping and ping.get("error"):
        issues.append("ping_failed")

    if retrans and retrans.get("rate_percent") is not None:
        if retrans["rate_percent"] > settings.retrans_alert_threshold_pct:
            issues.append("retrans_critical")
        elif retrans["rate_percent"] > settings.retrans_warn_threshold_pct:
            issues.append("retrans_high")

    if mtu and mtu.get("ok") and mtu.get("path_mtu") is not None:
        if mtu["path_mtu"] < settings.report_mtu_info_below:
            issues.append("mtu_low")
    elif mtu and not mtu.get("ok"):
        issues.append("mtu_undetermined")

    if "retrans_critical" in issues or len(issues) >= 3:
        return "KRITISCH", issues
    if issues:
        return "WARN", issues
    return "OK", issues


def classify_run(target_statuses: list[str], broadcast_warn: bool) -> str:
    """Gesamt-Status eines Laufs: schlechtester Ziel-Status, plus Broadcast-Warnung."""
    if "KRITISCH" in target_statuses:
        return "KRITISCH"
    if "WARN" in target_statuses or broadcast_warn:
        return "WARN"
    return "OK"


# --------------------------------------------------------------------------- #
# Sub-Test-Wrapper (jeweils bestehenden Service aufrufen, keine eigene Logik)
# --------------------------------------------------------------------------- #

def _run_ping(host: str) -> dict:
    stats, error = wan_service.measure(host, count=10, timeout_ms=1000)
    if stats is None:
        return {"ok": False, "loss_pct": None, "rtt_avg": None, "jitter_ms": None, "error": error}
    return {
        "ok": bool(stats.rtts) and stats.loss_pct < 100.0,
        "loss_pct": stats.loss_pct,
        "rtt_min": stats.rtt_min,
        "rtt_avg": stats.rtt_avg,
        "rtt_max": stats.rtt_max,
        "jitter_ms": stats.jitter_ms,
        "error": error,
    }


def _run_mtu(host: str) -> dict:
    data = mtu_service.discover_path_mtu(host, settings.mtu_min, settings.mtu_max)
    with session_scope() as db:
        db.add(MtuTest(
            target=data["target"], ok=data["ok"], path_mtu=data["path_mtu"],
            min_mtu=data["min_mtu"], max_mtu=data["max_mtu"],
            probe_count=data["probe_count"], error=data["error"],
        ))
    return data


def _run_traceroute(host: str) -> dict:
    with session_scope() as db:
        tr = traceroute_service.run(db, host=host, mode="icmp", resolve_geo=False, source="manual")
        first_hop = next((h.hostname or h.ip for h in tr.hops if h.hop_number == 1), None)
        return {
            "id": tr.id,
            "completed": tr.completed,
            "dest_reached": tr.dest_reached,
            "total_hops": tr.total_hops,
            "avg_latency_ms": tr.avg_latency_ms,
            "first_hop": first_hop,
            "error": tr.error,
        }


def _run_speedtest() -> dict:
    with session_scope() as db:
        res = speedtest_service.run_speedtest(db, source="manual")
        return {
            "ok": res.ok, "download_mbps": res.download_mbps, "upload_mbps": res.upload_mbps,
            "ping_ms": res.ping_ms, "jitter_ms": res.jitter_ms, "error": res.error,
        }


def _run_broadcast(iface: str, duration_s: int) -> dict:
    out_dir = settings.capture_dir / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"diag_broadcast_{int(utcnow().timestamp())}.pcapng"
    pcap = capture_service.run_capture_window(iface, out_path, duration_s, bpf=None)
    if pcap is None:
        return {"frame_distribution": None, "warnings": [], "error": "Mitschnitt fehlgeschlagen (siehe Log)."}
    try:
        analysis = capture_service.analyze(pcap, max_packets=settings.analysis_max_packets)
        relevant = ("high_broadcast", "high_multicast")
        return {
            "frame_distribution": analysis["frame_distribution"],
            "warnings": [w for w in analysis["warnings"] if w["code"] in relevant],
            "error": None,
        }
    finally:
        pcap.unlink(missing_ok=True)


def _run_retrans(
    iface: str, resolved: list[tuple[str, str, int]], duration_s: int
) -> tuple[dict, dict[str, dict]]:
    """Ein aktiver Retransmission-Test für ALLE Ziele gemeinsam (ein Mitschnitt)."""
    targets_payload = [{"host": ip, "port": port, "protocol": "tcp"} for _h, ip, port in resolved]
    with session_scope() as db:
        test = retransmission_service.run_test(
            db, interface=iface, duration_s=duration_s, source="manual",
            mode="active", targets=targets_payload, concurrent=True,
        )
        by_ip = {
            t.target_ip: {
                "rate_percent": t.rate_percent,
                "retransmissions": t.retransmissions,
                "packets": t.packets,
                "connection_errors": t.connection_errors,
            }
            for t in test.targets
        }
        summary = {
            "id": test.id, "status": test.status, "classification": test.classification,
            "rate_percent": test.retransmission_rate_percent, "error": test.error,
        }
        return summary, by_ip


def _collect(futs: dict[Future, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fut, key in futs.items():
        try:
            out[key] = fut.result()
        except Exception as exc:  # noqa: BLE001 - ein Sub-Test darf die anderen nicht blockieren
            out[key] = {"error": str(exc)}
    return out


def _collect_one(fut: Future | None) -> dict | None:
    if fut is None:
        return None
    try:
        return fut.result()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Orchestrierung
# --------------------------------------------------------------------------- #

def run_diagnostics(*, targets: list[dict], tests: dict, interface: str, duration_s: int) -> DiagnosticRun:
    iface = capture_service.validate_iface(interface)
    duration_s = max(5, min(int(duration_s), settings.retrans_max_duration_s))
    targets = targets[: settings.diagnostics_max_targets]
    tests = {k: bool(tests.get(k)) for k in TEST_TYPES}

    needs_targets = tests["ping"] or tests["retrans"] or tests["mtu"] or tests["traceroute"]
    if needs_targets and not targets:
        raise DiagnosticsError("Mindestens ein Ziel erforderlich für die ausgewählten Tests.")
    if not any(tests.values()):
        raise DiagnosticsError("Mindestens ein Test muss ausgewählt sein.")

    with session_scope() as db:
        run = DiagnosticRun(
            interface=iface, duration_s=duration_s,
            targets_json=json.dumps(targets), tests_json=json.dumps(tests), status="running",
        )
        db.add(run)
        db.flush()
        run_id = run.id

    resolved_for_retrans: list[tuple[str, str, int]] = []
    if tests["retrans"] and targets:
        for t in targets:
            try:
                ip, port = retransmission_service.resolve_active_target(
                    t["host"], _DEFAULT_RETRANS_PORT, "tcp"
                )
                resolved_for_retrans.append((t["host"], ip, port))
            except ValueError as exc:
                log.warning("Retransmission-Ziel %s übersprungen: %s", t["host"], exc)

    pool_size = max(1, len(targets) * 3 + 2)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        ping_futs = {pool.submit(_run_ping, t["host"]): t["host"] for t in targets} if tests["ping"] else {}
        mtu_futs = {pool.submit(_run_mtu, t["host"]): t["host"] for t in targets} if tests["mtu"] else {}
        tr_futs = (
            {pool.submit(_run_traceroute, t["host"]): t["host"] for t in targets}
            if tests["traceroute"] else {}
        )
        speedtest_fut = pool.submit(_run_speedtest) if tests["speedtest"] else None
        broadcast_fut = pool.submit(_run_broadcast, iface, duration_s) if tests["broadcast"] else None
        retrans_fut = (
            pool.submit(_run_retrans, iface, resolved_for_retrans, duration_s)
            if (tests["retrans"] and resolved_for_retrans) else None
        )

        ping_results = _collect(ping_futs)
        mtu_results = _collect(mtu_futs)
        tr_results = _collect(tr_futs)
        speedtest_result = _collect_one(speedtest_fut)
        broadcast_result = _collect_one(broadcast_fut)
        if retrans_fut is not None:
            try:
                retrans_summary, retrans_by_ip = retrans_fut.result()
            except Exception as exc:  # noqa: BLE001
                retrans_summary, retrans_by_ip = {"error": str(exc)}, {}
        else:
            retrans_summary, retrans_by_ip = None, {}

    host_to_ip = {h: ip for h, ip, _p in resolved_for_retrans}

    targets_out: list[dict] = []
    statuses: list[str] = []
    for t in targets:
        host = t["host"]
        ping = ping_results.get(host)
        mtu = mtu_results.get(host)
        tr = tr_results.get(host)
        retrans = retrans_by_ip.get(host_to_ip.get(host, "")) if host_to_ip else None
        status, issues = classify_target(ping, retrans, mtu)
        statuses.append(status)
        targets_out.append({
            "name": t.get("name") or host, "host": host,
            "ping": ping, "retrans": retrans, "mtu": mtu, "traceroute": tr,
            "status": status, "issues": issues,
        })

    broadcast_warn = bool(broadcast_result and broadcast_result.get("warnings"))
    retrans_error = retrans_summary.get("error") if retrans_summary else None
    # Ein komplett fehlgeschlagener Retransmission-Sub-Test (z. B. dumpcap fehlt)
    # betrifft alle Ziele gleichermaßen und wird daher nicht als Pro-Ziel-Problem
    # dupliziert, fließt aber in den Gesamtstatus ein - sonst wäre ein explizit
    # ausgewählter, aber nie gelaufener Test für den Nutzer unsichtbar.
    overall = classify_run(statuses, broadcast_warn or bool(retrans_error))

    results: dict[str, Any] = {
        "generated": to_local(utcnow()).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "interface": iface,
        "duration_s": duration_s,
        "tests": tests,
        "targets": targets_out,
        "broadcast": broadcast_result,
        "speedtest": speedtest_result,
        "retrans_test_id": retrans_summary.get("id") if retrans_summary else None,
        "retrans_error": retrans_error,
        "overall_status": overall,
    }

    with session_scope() as db:
        finished = db.get(DiagnosticRun, run_id)
        assert finished is not None
        finished.status = "done"
        finished.overall_status = overall
        finished.results_json = json.dumps(results)
        db.commit()
        db.refresh(finished)
        log.info(
            "Diagnose-Lauf %s fertig: %d Ziele, Status %s", finished.id, len(targets), overall
        )
        return finished
