"""Report-Generierung.

Sammelt die passive PCAP-Analyse **und** die Ergebnisse aller aktiven Tests
(Ping/WAN, Speedtest, iperf3 TCP/UDP, MTU) zu einem JSON-Snapshot, persistiert
diesen am Report-Modell und rendert daraus einen druckfreundlichen HTML-Report
(optional zusätzlich PDF via WeasyPrint).

- Kein neuer Pflicht-Import: WeasyPrint wird nur bei Bedarf importiert.
- Alle dynamischen Werte werden HTML-escaped.
- Schwellwerte für Warnungen: zentral in ``app/core/config.py`` (``report_*``,
  ``analysis_*``).
"""

from __future__ import annotations

import html
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import to_local, utcnow
from app.models.capture import Capture
from app.models.capture_job import CaptureJob
from app.models.iperf import IperfResult
from app.models.mtu import MtuTest
from app.models.ping import PingJob, PingSample
from app.models.report import Report
from app.models.retransmission import RetransmissionTest
from app.models.speedtest import SpeedtestResult
from app.models.wan import WanResult, WanTarget
from app.services import capture_service
from app.services.long_capture import list_job_files, resolve_job_file
from app.services.parsers import compute_jitter

log = get_logger("netprobe.report")

# Auswählbare Report-Abschnitte (Reihenfolge = Darstellung).
REPORT_SECTIONS: tuple[str, ...] = (
    "summary", "ping", "speedtest", "iperf3", "mtu", "passive", "retransmission",
)
SECTION_LABELS = {
    "summary": "Zusammenfassung (Key Metrics)",
    "ping": "Ping & WAN-Qualität",
    "speedtest": "Speedtest",
    "iperf3": "iperf3 (TCP/UDP)",
    "mtu": "MTU-Test",
    "passive": "Passive Analyse (PCAP)",
    "retransmission": "TCP-Retransmissions",
}
_ACTIVE_SECTIONS = ("ping", "speedtest", "iperf3", "mtu")


class ReportError(ValueError):
    pass


def report_paths(report_id: int) -> tuple[Path, Path]:
    base = settings.reports_dir
    return base / f"{report_id}.html", base / f"{report_id}.pdf"


# --------------------------------------------------------------------------- #
# Formatierung
# --------------------------------------------------------------------------- #

def _fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "–"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.1f} {unit}".replace(".", ",")
        v /= 1024
    return f"{v:.1f} TB".replace(".", ",")


def _fmt_dt(dt) -> str:
    return to_local(dt).strftime("%Y-%m-%d %H:%M:%S") if dt else "–"


def _de(v: float | int | None, digits: int = 1, unit: str = "") -> str:
    if v is None:
        return "–"
    s = f"{v:.{digits}f}".replace(".", ",")
    return f"{s} {unit}".strip() if unit else s


def _pct(v: float | None, digits: int = 1) -> str:
    return _de(v, digits, "%")


def _grp(n: int | None) -> str:
    """Ganzzahl mit Tausenderpunkt (deutsch)."""
    return "–" if n is None else f"{n:,}".replace(",", ".")


# --------------------------------------------------------------------------- #
# Quelle
# --------------------------------------------------------------------------- #

def _resolve_source(db: Session, capture_id: int | None, job_id: int | None) -> tuple[Path, dict]:
    if capture_id is not None:
        cap = db.get(Capture, capture_id)
        if cap is None or not cap.path:
            raise ReportError("Capture nicht gefunden oder ohne Datei.")
        path = Path(cap.path)
        try:
            path.resolve().relative_to(settings.capture_dir.resolve())
        except ValueError as exc:
            raise ReportError("Ungültiger Capture-Pfad.") from exc
        if not path.exists():
            raise ReportError("Capture-Datei fehlt.")
        return path, {
            "kind": "Capture",
            "ref": f"#{cap.id}",
            "iface": cap.iface,
            "ts": _fmt_dt(cap.ts),
            "size": _fmt_bytes(cap.size_bytes),
            "bpf": cap.bpf or "–",
        }

    if job_id is not None:
        job = db.get(CaptureJob, job_id)
        if job is None:
            raise ReportError("Langzeit-Capture-Job nicht gefunden.")
        files = list_job_files(job_id)
        if not files:
            raise ReportError("Der Job hat noch keine PCAP-Dateien geschrieben.")
        newest = files[-1]["name"]
        job_path = resolve_job_file(job_id, newest)
        if job_path is None:
            raise ReportError("PCAP-Datei des Jobs nicht auffindbar.")
        return job_path, {
            "kind": "Langzeit-Capture",
            "ref": f"Job #{job.id} ({job.name})" if job.name else f"Job #{job.id}",
            "iface": job.iface,
            "ts": _fmt_dt(job.created_at),
            "size": _fmt_bytes(job_path.stat().st_size),
            "bpf": job.bpf or "–",
            "file": newest,
        }

    raise ReportError("Es muss capture_id oder job_id angegeben werden.")


# --------------------------------------------------------------------------- #
# Retransmission-Test -> Snapshot (eigenständig, ohne PCAP-Grundanalyse)
# --------------------------------------------------------------------------- #

def _retrans_snapshot(db: Session, retrans_test_id: int) -> dict:
    test = db.get(RetransmissionTest, retrans_test_id)
    if test is None:
        raise ReportError("Retransmission-Test nicht gefunden.")

    mode_label = "Aktiv" if test.test_mode == "active" else "Passiv"
    if test.test_mode == "active":
        mode_label += " (parallel)" if test.concurrent else " (nacheinander)"
    source = {
        "kind": "Retransmission-Test",
        "ref": f"#{test.id}",
        "iface": test.interface,
        "ts": _fmt_dt(test.ts),
        "size": f"{test.duration_s} s · {mode_label}",
        "bpf": test.bpf or "–",
    }

    rate = test.retransmission_rate_percent or 0.0
    warn = rate >= settings.retrans_warn_threshold_pct
    lvl = "warn" if warn else ("ok" if test.status == "done" else "na")
    targets = sorted(test.targets, key=lambda t: t.retransmissions, reverse=True)

    key_metrics = [
        _km(
            "Retransmission-Rate", _pct(rate, 2),
            f'{_grp(test.total_retransmissions)} von {_grp(test.total_packets)} Paketen', lvl,
        ),
        _km(
            "Klassifikation", test.classification or "–", "automatische Einordnung",
            "warn" if test.classification not in (None, "OK") else "ok",
        ),
        _km("Interface", test.interface, test.bpf or "alle TCP-Pakete", "na"),
    ]

    interpretations: list[dict] = []
    if test.error:
        interpretations.append({"level": "warn", "text": f"Test fehlgeschlagen: {test.error}"})
    if test.recommendation:
        interpretations.append({"level": "warn" if warn else "info", "text": test.recommendation})
    for t in targets[:10]:
        kind = "LAN" if t.is_private else "WAN"
        ref = f"{t.target_ip}:{t.target_port}" if t.target_port else t.target_ip
        interpretations.append({
            "level": "warn" if t.rate_percent >= settings.retrans_warn_threshold_pct else "info",
            "text": f'{ref} ({kind}, {t.direction}): {_grp(t.retransmissions)} Retransmissions '
                    f'({_pct(t.rate_percent)}).',
        })

    retransmission = {
        "status": test.status,
        "test_mode": test.test_mode,
        "classification": test.classification,
        "recommendation": test.recommendation,
        "total_packets": test.total_packets,
        "total_retransmissions": test.total_retransmissions,
        "rate_percent": rate,
        "targets": [
            {
                "ip": t.target_ip, "port": t.target_port, "is_private": t.is_private,
                "retransmissions": t.retransmissions, "packets": t.packets,
                "rate_percent": t.rate_percent, "direction": t.direction,
                "connection_attempts": t.connection_attempts,
                "connection_errors": t.connection_errors,
                "avg_connect_latency_ms": t.avg_connect_latency_ms,
            }
            for t in targets
        ],
    }

    return {
        "generated": to_local(utcnow()).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "netprobe_version": __version__,
        "source": source,
        "sections": ["summary", "retransmission"],
        "key_metrics": key_metrics,
        "interpretations": interpretations,
        "active": {},
        "passive": None,
        "retransmission": retransmission,
    }


# --------------------------------------------------------------------------- #
# Aktive Tests einsammeln (JSON-serialisierbare Dicts)
# --------------------------------------------------------------------------- #

def _ping_summaries(db: Session) -> list[dict]:
    since = utcnow() - timedelta(hours=settings.report_ping_window_hours)
    out: list[dict] = []
    for job in db.execute(select(PingJob).order_by(PingJob.id)).scalars().all():
        samples = list(
            db.execute(
                select(PingSample)
                .where(PingSample.job_id == job.id, PingSample.ts >= since)
                .order_by(PingSample.ts)
                .limit(50000)
            ).scalars().all()
        )
        if not samples:
            continue
        rtts = [s.rtt_ms for s in samples if s.ok and s.rtt_ms is not None]
        loss = sum(1 for s in samples if not s.ok)
        out.append({
            "name": job.name,
            "host": job.host,
            "status": job.status,
            "count": len(samples),
            "loss": loss,
            "loss_pct": round(loss / len(samples) * 100, 2) if samples else 0.0,
            "rtt_min": round(min(rtts), 2) if rtts else None,
            "rtt_avg": round(sum(rtts) / len(rtts), 2) if rtts else None,
            "rtt_max": round(max(rtts), 2) if rtts else None,
            "jitter_ms": compute_jitter(rtts) if len(rtts) >= 2 else None,
        })
    return out


def _wan_summaries(db: Session) -> list[dict]:
    out: list[dict] = []
    for target in db.execute(select(WanTarget).order_by(WanTarget.id)).scalars().all():
        last = db.execute(
            select(WanResult)
            .where(WanResult.target_id == target.id)
            .order_by(WanResult.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last is None:
            continue
        out.append({
            "name": target.name,
            "host": target.host,
            "ts": _fmt_dt(last.ts),
            "ok": last.ok,
            "rtt_avg": last.rtt_avg,
            "rtt_max": last.rtt_max,
            "jitter_ms": last.jitter_ms,
            "loss_pct": last.loss_pct,
        })
    return out


def _speedtest_latest(db: Session) -> dict | None:
    sp = db.execute(
        select(SpeedtestResult).order_by(SpeedtestResult.ts.desc()).limit(1)
    ).scalar_one_or_none()
    if sp is None:
        return None
    return {
        "ts": _fmt_dt(sp.ts),
        "ok": sp.ok,
        "download_mbps": sp.download_mbps,
        "upload_mbps": sp.upload_mbps,
        "ping_ms": sp.ping_ms,
        "jitter_ms": sp.jitter_ms,
        "packet_loss": sp.packet_loss,
        "server_name": sp.server_name,
        "error": sp.error,
    }


def _iperf_row(r: IperfResult) -> dict:
    return {
        "ts": _fmt_dt(r.ts),
        "ok": r.ok,
        "host": r.host,
        "port": r.port,
        "reverse": r.reverse,
        "parallel": r.parallel,
        "duration_s": r.duration_s,
        "sent_mbps": r.sent_mbps,
        "recv_mbps": r.recv_mbps,
        "jitter_ms": r.jitter_ms,
        "lost_percent": r.lost_percent,
        "lost_packets": r.lost_packets,
        "packets": r.packets,
        "out_of_order": r.out_of_order,
        "retransmits": r.retransmits,
        "error": r.error,
    }


def _iperf_split(db: Session, since) -> tuple[list[dict], list[dict]]:
    rows = list(
        db.execute(
            select(IperfResult)
            .where(IperfResult.ts >= since)
            .order_by(IperfResult.ts.desc())
            .limit(30)
        ).scalars().all()
    )
    tcp = [_iperf_row(r) for r in rows if r.protocol == "tcp"][:5]
    udp = [_iperf_row(r) for r in rows if r.protocol == "udp"][:5]
    return tcp, udp


def _mtu_recent(db: Session, since) -> list[dict]:
    rows = db.execute(
        select(MtuTest).where(MtuTest.ts >= since).order_by(MtuTest.ts.desc()).limit(5)
    ).scalars().all()
    return [
        {
            "ts": _fmt_dt(m.ts),
            "target": m.target,
            "ok": m.ok,
            "path_mtu": m.path_mtu,
            "min_mtu": m.min_mtu,
            "max_mtu": m.max_mtu,
            "probe_count": m.probe_count,
            "error": m.error,
        }
        for m in rows
    ]


def _collect_active(db: Session, sections: list[str]) -> dict:
    since = utcnow() - timedelta(hours=settings.report_test_lookback_hours)
    active: dict = {}
    if "ping" in sections:
        active["ping"] = _ping_summaries(db)
        active["wan"] = _wan_summaries(db)
    if "speedtest" in sections:
        active["speedtest"] = _speedtest_latest(db)
    if "iperf3" in sections:
        active["iperf_tcp"], active["iperf_udp"] = _iperf_split(db, since)
    if "mtu" in sections:
        active["mtu"] = _mtu_recent(db, since)
    return active


# --------------------------------------------------------------------------- #
# Passive Analyse -> kompakter Snapshot
# --------------------------------------------------------------------------- #

def _passive_snapshot(analysis: dict) -> dict:
    return {
        "packet_count": analysis.get("packet_count"),
        "packets_analyzed": analysis.get("packets_analyzed"),
        "sampled": analysis.get("sampled", False),
        "frame_distribution": analysis["frame_distribution"],
        "tcp_health": analysis["tcp_health"],
        "warnings": analysis.get("warnings", []),
        "top_protocols": analysis["top_protocols"][:12],
        "top_conversations": analysis["top_conversations"][:12],
        "protocol_hierarchy": analysis["protocol_hierarchy"][:25],
        "dns_queries": analysis.get("dns_queries", [])[:15],
        "tls_sni": analysis.get("tls_sni", [])[:15],
        "expert_infos": analysis.get("expert_infos", [])[:15],
    }


# --------------------------------------------------------------------------- #
# Key Metrics + Interpretationen
# --------------------------------------------------------------------------- #

def _level(cond_warn: bool, cond_info: bool = False) -> str:
    if cond_warn:
        return "warn"
    return "info" if cond_info else "ok"


def _km(label: str, value: str, sub: str, level: str) -> dict:
    return {"label": label, "value": value, "sub": sub, "level": level}


def _build_key_metrics(passive: dict | None, active: dict) -> list[dict]:
    metrics: list[dict] = []

    # 1) Paketverlust Ping (schlechtester Job)
    pings = active.get("ping") or []
    if pings:
        worst = max(pings, key=lambda p: p["loss_pct"])
        lvl = "warn" if worst["loss_pct"] > settings.report_ping_loss_warn_pct else "ok"
        metrics.append(_km("Paketverlust Ping", _pct(worst["loss_pct"]),
                           worst["name"] or worst["host"], lvl))
    else:
        metrics.append(_km("Paketverlust Ping", "–", "nicht einbezogen", "na"))

    # 2) UDP-Verlust (aktuellster iperf3-UDP-Test)
    udp = active.get("iperf_udp") or []
    if udp and udp[0].get("lost_percent") is not None:
        lp = udp[0]["lost_percent"]
        lvl = "warn" if lp > settings.report_udp_loss_warn_pct else "ok"
        metrics.append(_km("UDP-Verlust (iperf3)", _pct(lp), udp[0]["host"], lvl))
    else:
        metrics.append(_km("UDP-Verlust (iperf3)", "–", "kein UDP-Test", "na"))

    # 3) TCP-Retransmissions (aktuellster iperf3-TCP-Test)
    tcp = active.get("iperf_tcp") or []
    if tcp and tcp[0].get("retransmits") is not None:
        rt = tcp[0]["retransmits"]
        lvl = "warn" if rt > settings.report_tcp_retransmit_warn else "ok"
        metrics.append(_km("TCP-Retransmissions", _grp(rt), tcp[0]["host"], lvl))
    else:
        metrics.append(_km("TCP-Retransmissions", "–", "kein TCP-Test", "na"))

    # 4) Broadcast-Anteil (passive Analyse)
    if passive:
        bp = passive["frame_distribution"]["broadcast_pct"]
        lvl = "warn" if bp > settings.analysis_broadcast_warn_pct else "ok"
        metrics.append(_km("Broadcast-Anteil", _pct(bp), "Sicherungsschicht", lvl))
    else:
        metrics.append(_km("Broadcast-Anteil", "–", "keine PCAP-Analyse", "na"))

    # 5) Speedtest-Download
    sp = active.get("speedtest")
    if sp and sp.get("download_mbps") is not None:
        metrics.append(_km("Speedtest-Download", _de(sp["download_mbps"], 1, "Mbit/s"),
                           f'Upload {_de(sp.get("upload_mbps"), 1)}', "info"))
    else:
        metrics.append(_km("Speedtest-Download", "–", "kein Speedtest", "na"))

    return metrics


def _build_interpretations(passive: dict | None, active: dict) -> list[dict]:
    out: list[dict] = []

    for p in active.get("ping") or []:
        name = p["name"] or p["host"]
        if p["loss_pct"] > settings.report_ping_loss_warn_pct:
            out.append({"level": "warn", "text":
                f'Paketverlust im Ping-Test ({name}): {_pct(p["loss_pct"])} – erhöht '
                f'(Schwelle {_pct(settings.report_ping_loss_warn_pct)}).'})
        else:
            out.append({"level": "ok", "text":
                f'Paketverlust im Ping-Test ({name}): {_pct(p["loss_pct"])} – unauffällig.'})
        if p.get("jitter_ms") is not None and p["jitter_ms"] > settings.report_jitter_warn_ms:
            out.append({"level": "warn", "text":
                f'Ping-Jitter ({name}): {_de(p["jitter_ms"], 1, "ms")} – erhöht.'})

    sp = active.get("speedtest")
    if sp:
        out.append({"level": "info", "text":
            f'Speedtest: Download {_de(sp.get("download_mbps"), 1, "Mbit/s")}, '
            f'Upload {_de(sp.get("upload_mbps"), 1, "Mbit/s")}, '
            f'Latenz {_de(sp.get("ping_ms"), 1, "ms")}, Jitter {_de(sp.get("jitter_ms"), 1, "ms")}.'})
        if sp.get("jitter_ms") is not None and sp["jitter_ms"] > settings.report_jitter_warn_ms:
            out.append({"level": "warn", "text":
                f'Speedtest-Jitter: {_de(sp["jitter_ms"], 1, "ms")} – erhöht.'})

    udp = active.get("iperf_udp") or []
    if udp:
        u = udp[0]
        if u.get("lost_percent") is not None:
            if u["lost_percent"] > settings.report_udp_loss_warn_pct:
                out.append({"level": "warn", "text":
                    f'iperf3-UDP-Paketverlust ({u["host"]}): {_pct(u["lost_percent"])} – erhöht '
                    f'(Schwelle {_pct(settings.report_udp_loss_warn_pct)}).'})
            else:
                out.append({"level": "ok", "text":
                    f'iperf3-UDP-Paketverlust ({u["host"]}): {_pct(u["lost_percent"])} – unauffällig.'})
        if u.get("out_of_order"):
            out.append({"level": "info", "text":
                f'iperf3-UDP: {u["out_of_order"]} Pakete out-of-order.'})
        if u.get("jitter_ms") is not None and u["jitter_ms"] > settings.report_jitter_warn_ms:
            out.append({"level": "warn", "text":
                f'iperf3-UDP-Jitter ({u["host"]}): {_de(u["jitter_ms"], 1, "ms")} – erhöht.'})

    tcp = active.get("iperf_tcp") or []
    if tcp:
        t = tcp[0]
        if t.get("retransmits") is not None and t["retransmits"] > settings.report_tcp_retransmit_warn:
            out.append({"level": "warn", "text":
                f'iperf3-TCP-Retransmissions ({t["host"]}): {t["retransmits"]} – erhöht '
                f'(Schwelle {settings.report_tcp_retransmit_warn}).'})
        else:
            out.append({"level": "info", "text":
                f'iperf3-TCP: {_de(t.get("sent_mbps"), 1, "Mbit/s")} Durchsatz, '
                f'{t.get("retransmits") if t.get("retransmits") is not None else "?"} Retransmissions.'})

    for m in active.get("mtu") or []:
        if m["ok"] and m["path_mtu"] is not None:
            lvl = "info" if m["path_mtu"] >= settings.report_mtu_info_below else "warn"
            extra = "" if lvl == "info" else " – kleiner als 1500, Fragmentierung/PMTUD-Probleme möglich"
            out.append({"level": lvl,
                        "text": f'Pfad-MTU zu {m["target"]}: {m["path_mtu"]} Byte{extra}.'})
        else:
            out.append({"level": "warn",
                        "text": f'MTU-Test zu {m["target"]}: keine Pfad-MTU ermittelt.'})

    if passive:
        for w in passive.get("warnings", []):
            out.append({"level": w["level"], "text": w["message"]})
        th = passive["tcp_health"]
        out.append({"level": "info", "text":
            f'Passive TCP-Analyse: {_grp(th["retransmissions"])} Retransmissions '
            f'({_pct(th["retransmission_pct"])}), {_grp(th["resets"])} RST-Pakete.'})

    return out


# --------------------------------------------------------------------------- #
# Snapshot + Report erzeugen
# --------------------------------------------------------------------------- #

def build_snapshot(
    db: Session,
    *,
    capture_id: int | None,
    job_id: int | None,
    retrans_test_id: int | None = None,
    sections: list[str],
) -> dict:
    if retrans_test_id is not None:
        return _retrans_snapshot(db, retrans_test_id)

    valid = [s for s in sections if s in SECTION_LABELS] or list(REPORT_SECTIONS)
    path, source = _resolve_source(db, capture_id, job_id)

    passive = None
    if "passive" in valid or "summary" in valid:
        # "summary" braucht den Broadcast-Anteil -> passive Analyse mitnehmen.
        passive = _passive_snapshot(capture_service.analyze(path))

    active = _collect_active(db, valid)
    key_metrics = _build_key_metrics(passive, active)
    interpretations = _build_interpretations(passive, active)

    return {
        "generated": to_local(utcnow()).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "netprobe_version": __version__,
        "source": source,
        "sections": valid,
        "key_metrics": key_metrics,
        "interpretations": interpretations,
        "active": active,
        "passive": passive,
    }


def generate(
    db: Session,
    *,
    capture_id: int | None,
    job_id: int | None,
    customer: str,
    location: str,
    notes: str | None,
    sections: list[str],
    retrans_test_id: int | None = None,
) -> Report:
    snapshot = build_snapshot(
        db, capture_id=capture_id, job_id=job_id, retrans_test_id=retrans_test_id, sections=sections
    )

    title = f"netprobe-Report – {customer}" if customer else "netprobe-Report"
    report = Report(
        title=title,
        capture_id=capture_id,
        job_id=job_id,
        retrans_test_id=retrans_test_id,
        customer=customer,
        location=location,
        notes=notes,
        sections=json.dumps(snapshot["sections"]),
        data_json=json.dumps(snapshot),
        has_pdf=False,
    )
    db.add(report)
    db.flush()  # ID vergeben

    html_str = render_report_html(report, snapshot)

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path = report_paths(report.id)
    html_path.write_text(html_str, encoding="utf-8")

    err = pdf_from_html(html_str, pdf_path)
    report.has_pdf = err is None
    report.error = err
    db.commit()
    db.refresh(report)
    log.info("Report %s erstellt (pdf=%s)", report.id, report.has_pdf)
    return report


def pdf_from_html(html_str: str, pdf_path: Path) -> str | None:
    """Rendert HTML -> PDF via WeasyPrint. Rückgabe: Fehlermeldung oder ``None``."""
    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 - WeasyPrint optional/nicht installiert
        return "PDF nicht erzeugt: WeasyPrint ist nicht installiert (nur HTML verfügbar)."
    try:
        HTML(string=html_str).write_pdf(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - Rendering-Fehler nicht fatal
        log.warning("PDF-Rendering fehlgeschlagen: %s", exc)
        return f"PDF-Rendering fehlgeschlagen: {exc}"
    return None


# --------------------------------------------------------------------------- #
# HTML-Template (druckfreundlich, ohne externe Ressourcen)
# --------------------------------------------------------------------------- #

_CSS = """
* { box-sizing: border-box; }
body {
  font: 13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
  color: #1e293b; margin: 0; padding: 32px;
}
h1 { font-size: 22px; margin: 0 0 4px; }
h2 {
  font-size: 16px; margin: 26px 0 10px;
  border-bottom: 2px solid #cbd5e1; padding-bottom: 4px;
}
h3 { font-size: 13px; margin: 16px 0 6px; color: #475569; }
.meta { color: #64748b; margin-bottom: 8px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 14px; }
th, td {
  text-align: left; padding: 5px 8px;
  border-bottom: 1px solid #e2e8f0; font-size: 12px;
}
th { background: #f1f5f9; color: #475569; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 14px; }
.tile {
  border: 1px solid #cbd5e1; border-radius: 8px;
  padding: 10px 14px; min-width: 130px;
}
.tile.warn { border-color: #f59e0b; background: #fffbeb; }
.tile.ok { border-color: #86efac; }
.tile.na { opacity: .6; }
.tile .l {
  color: #64748b; font-size: 11px;
  text-transform: uppercase; letter-spacing: .04em;
}
.tile .v { font-size: 20px; font-weight: 700; }
.tile .s { color: #64748b; font-size: 11px; }
.box { border-radius: 8px; padding: 7px 12px; margin: 5px 0; font-size: 12px; }
.box.warn { border: 1px solid #f59e0b; background: #fffbeb; }
.box.info { border: 1px solid #93c5fd; background: #eff6ff; }
.box.ok { border: 1px solid #86efac; background: #f0fdf4; }
.foot {
  margin-top: 32px; color: #94a3b8; font-size: 11px;
  border-top: 1px solid #e2e8f0; padding-top: 8px;
}
@page { size: A4; margin: 16mm; }
@media print {
  body { padding: 0; }
  h2 { page-break-after: avoid; }
  table { page-break-inside: avoid; }
}
"""


def _e(v: object) -> str:
    return html.escape("" if v is None else str(v))


def _rows(headers: list[str], rows: list[list[str]], num_from: int = 1) -> str:
    head = "".join(
        f'<th class="{"num" if i >= num_from else ""}">{_e(h)}</th>' for i, h in enumerate(headers)
    )
    body = ""
    for r in rows:
        cells = "".join(
            f'<td class="{"num" if i >= num_from else ""}">{_e(c)}</td>' for i, c in enumerate(r)
        )
        body += f"<tr>{cells}</tr>"
    if not rows:
        body = f'<tr><td colspan="{len(headers)}">–</td></tr>'
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _boxes(items: list[dict]) -> str:
    return "".join(f'<div class="box {i["level"]}">{_e(i["text"])}</div>' for i in items)


def _render_active(active: dict, sections: list[str]) -> list[str]:
    parts: list[str] = []

    if "ping" in sections and (active.get("ping") or active.get("wan")):
        parts.append(f'<h2>{SECTION_LABELS["ping"]}</h2>')
        if active.get("ping"):
            parts.append(f'<h3>Dauerping (letzte {settings.report_ping_window_hours} h)</h3>')
            parts.append(_rows(
                ["Job", "Host", "Samples", "Verlust", "RTT min", "RTT ø", "RTT max", "Jitter"],
                [[
                    p["name"], p["host"], _grp(p["count"]),
                    _pct(p["loss_pct"]), _de(p["rtt_min"], 1, "ms"), _de(p["rtt_avg"], 1, "ms"),
                    _de(p["rtt_max"], 1, "ms"), _de(p["jitter_ms"], 1, "ms"),
                ] for p in active["ping"]],
                num_from=2,
            ))
        if active.get("wan"):
            parts.append("<h3>WAN-Qualität (aktuellster Messwert je Ziel)</h3>")
            parts.append(_rows(
                ["Ziel", "Host", "Zeit", "RTT ø", "Jitter", "Verlust"],
                [[
                    w["name"], w["host"], w["ts"], _de(w["rtt_avg"], 1, "ms"),
                    _de(w["jitter_ms"], 1, "ms"), _pct(w["loss_pct"]),
                ] for w in active["wan"]],
                num_from=3,
            ))

    if "speedtest" in sections:
        sp = active.get("speedtest")
        parts.append(f'<h2>{SECTION_LABELS["speedtest"]}</h2>')
        if sp:
            parts.append(_rows(
                ["Zeit", "Server", "Download", "Upload", "Latenz", "Jitter", "Verlust"],
                [[
                    sp["ts"], sp.get("server_name") or "auto",
                    _de(sp.get("download_mbps"), 1, "Mbit/s"), _de(sp.get("upload_mbps"), 1, "Mbit/s"),
                    _de(sp.get("ping_ms"), 1, "ms"), _de(sp.get("jitter_ms"), 1, "ms"),
                    _pct(sp.get("packet_loss")),
                ]],
                num_from=2,
            ))
        else:
            parts.append('<div class="box info">Kein Speedtest-Ergebnis vorhanden.</div>')

    if "iperf3" in sections:
        parts.append(f'<h2>{SECTION_LABELS["iperf3"]}</h2>')
        tcp = active.get("iperf_tcp") or []
        udp = active.get("iperf_udp") or []
        parts.append("<h3>TCP</h3>")
        parts.append(_rows(
            ["Zeit", "Ziel", "Richtung", "Senden", "Empfangen", "Retransmissions"],
            [[
                t["ts"], f'{t["host"]}:{t["port"]}', "Download" if t["reverse"] else "Upload",
                _de(t["sent_mbps"], 1, "Mbit/s"), _de(t["recv_mbps"], 1, "Mbit/s"),
                t["retransmits"] if t["retransmits"] is not None else "–",
            ] for t in tcp],
            num_from=3,
        ))
        parts.append("<h3>UDP</h3>")
        parts.append(_rows(
            ["Zeit", "Ziel", "Richtung", "Durchsatz", "Jitter", "Verlust", "verl. Pakete", "Out-of-Order"],
            [[
                u["ts"], f'{u["host"]}:{u["port"]}', "Download" if u["reverse"] else "Upload",
                _de(u["sent_mbps"], 1, "Mbit/s"), _de(u["jitter_ms"], 2, "ms"),
                _pct(u["lost_percent"]),
                f'{u["lost_packets"]}/{u["packets"]}' if u["packets"] else "–",
                u["out_of_order"] if u["out_of_order"] is not None else "–",
            ] for u in udp],
            num_from=3,
        ))

    if "mtu" in sections:
        parts.append(f'<h2>{SECTION_LABELS["mtu"]}</h2>')
        parts.append(_rows(
            ["Zeit", "Ziel", "Pfad-MTU", "Bereich", "Proben"],
            [[
                m["ts"], m["target"],
                f'{m["path_mtu"]} Byte' if m["ok"] else "n. e.",
                f'{m["min_mtu"]}–{m["max_mtu"]}', str(m["probe_count"]),
            ] for m in (active.get("mtu") or [])],
            num_from=2,
        ))

    return parts


def _render_passive(p: dict, sections: list[str]) -> list[str]:
    parts: list[str] = [f'<h2>{SECTION_LABELS["passive"]}</h2>']
    if p.get("sampled"):
        total = _grp(p["packet_count"]) if p.get("packet_count") else "?"
        parts.append(
            f'<div class="box info">Grundanalyse auf Basis der ersten '
            f'{_grp(p["packets_analyzed"])} von {total} Paketen.</div>'
        )
    d = p["frame_distribution"]
    parts.append("<h3>Frame-Verteilung (Sicherungsschicht)</h3>")
    parts.append(_rows(
        ["Kategorie", "Pakete", "Anteil"],
        [
            ["Unicast", _grp(d["unicast"]), _pct(d["unicast_pct"])],
            ["Broadcast", _grp(d["broadcast"]), _pct(d["broadcast_pct"])],
            ["Multicast", _grp(d["multicast"]), _pct(d["multicast_pct"])],
            ["ohne Ethernet", _grp(d["other"]), _pct(d["other_pct"])],
        ],
        num_from=1,
    ))
    parts.append("<h3>Top-Protokolle</h3>")
    parts.append(_rows(
        ["Protokoll", "Pakete", "Bytes"],
        [[x["protocol"], _grp(x["packets"]), _fmt_bytes(x["bytes"])] for x in p["top_protocols"]],
    ))
    parts.append("<h3>Top-IP-Conversations</h3>")
    parts.append(_rows(
        ["Endpunkt A", "Endpunkt B", "Pakete", "Bytes"],
        [[c["addr_a"], c["addr_b"], _grp(c["packets"]), _fmt_bytes(c["bytes"])]
         for c in p["top_conversations"]],
        num_from=2,
    ))
    th = p["tcp_health"]
    parts.append("<h3>TCP-Gesundheit</h3>")
    parts.append(_rows(
        ["Kennzahl", "Wert"],
        [
            ["TCP-Pakete", _grp(th["tcp_packets"])],
            ["Retransmissions", f'{_grp(th["retransmissions"])} ({_pct(th["retransmission_pct"])})'],
            ["Duplicate ACKs", f'{_grp(th["dup_acks"])} ({_pct(th["dup_ack_pct"])})'],
            ["RST-Pakete", _grp(th["resets"])],
        ],
    ))
    if p.get("expert_infos"):
        parts.append("<h3>Expert Infos</h3>")
        parts.append(_rows(
            ["Schweregrad", "Protokoll", "Meldung", "Anzahl"],
            [[e["severity"], e["protocol"], e["summary"], str(e["count"])] for e in p["expert_infos"]],
            num_from=3,
        ))
    _ = sections  # zurzeit keine Unterauswahl innerhalb "passive"
    return parts


def _render_retransmission(r: dict) -> list[str]:
    parts: list[str] = [f'<h2>{SECTION_LABELS["retransmission"]}</h2>']
    parts.append(_rows(
        ["Kennzahl", "Wert"],
        [
            ["Pakete gesamt", _grp(r["total_packets"])],
            ["Retransmissions", _grp(r["total_retransmissions"])],
            ["Rate", _pct(r["rate_percent"], 2)],
            ["Klassifikation", r["classification"] or "–"],
        ],
    ))
    if r.get("recommendation"):
        parts.append(f'<div class="box info">{_e(r["recommendation"])}</div>')

    active = r.get("test_mode") == "active"
    parts.append("<h3>Getestete Ziele</h3>" if active else "<h3>Betroffene Ziele</h3>")
    headers = ["Ziel", "Netz", "Richtung", "Retransmissions", "Pakete", "Rate"]
    if active:
        headers += ["Verbindungen", "Fehler", "Latenz ø"]
    parts.append(_rows(
        headers,
        [
            [
                f'{t["ip"]}:{t["port"]}' if t["port"] else t["ip"],
                "LAN" if t["is_private"] else "WAN",
                t["direction"],
                _grp(t["retransmissions"]),
                _grp(t["packets"]),
                _pct(t["rate_percent"], 2),
                *(
                    [
                        _grp(t.get("connection_attempts")),
                        _grp(t.get("connection_errors")),
                        _de(t.get("avg_connect_latency_ms"), 1, "ms"),
                    ]
                    if active
                    else []
                ),
            ]
            for t in r["targets"][:30]
        ],
        num_from=3,
    ))
    return parts


def render_report_html(report: Report, snapshot: dict) -> str:
    sections: list[str] = snapshot["sections"]
    src: dict = snapshot["source"]
    parts: list[str] = []

    parts.append(f"<h1>{_e(report.title)}</h1>")
    meta = [f"Erstellt: {_e(snapshot['generated'])}", f"Report-ID: {_e(report.id)}"]
    if report.customer:
        meta.append(f"Kunde: {_e(report.customer)}")
    if report.location:
        meta.append(f"Standort: {_e(report.location)}")
    parts.append(f'<div class="meta">{" &nbsp;·&nbsp; ".join(meta)}</div>')
    parts.append(
        f'<div class="meta">Quelle: {_e(src["kind"])} {_e(src["ref"])} '
        f'&nbsp;·&nbsp; Interface {_e(src["iface"])} &nbsp;·&nbsp; {_e(src["size"])} '
        f'&nbsp;·&nbsp; Filter: {_e(src["bpf"])}</div>'
    )

    if "summary" in sections:
        parts.append(f'<h2>{SECTION_LABELS["summary"]}</h2>')
        parts.append('<div class="tiles">')
        for m in snapshot["key_metrics"]:
            parts.append(
                f'<div class="tile {_e(m["level"])}"><div class="l">{_e(m["label"])}</div>'
                f'<div class="v">{_e(m["value"])}</div><div class="s">{_e(m["sub"])}</div></div>'
            )
        parts.append("</div>")
        if snapshot["interpretations"]:
            parts.append("<h3>Interpretation</h3>")
            parts.append(_boxes(snapshot["interpretations"]))

    active = snapshot.get("active") or {}
    if any(s in sections for s in _ACTIVE_SECTIONS):
        parts.extend(_render_active(active, sections))

    if "passive" in sections and snapshot.get("passive"):
        parts.extend(_render_passive(snapshot["passive"], sections))

    if "retransmission" in sections and snapshot.get("retransmission"):
        parts.extend(_render_retransmission(snapshot["retransmission"]))

    if report.notes:
        parts.append("<h2>Notizen</h2>")
        parts.append(f"<p>{_e(report.notes).replace(chr(10), '<br>')}</p>")

    parts.append(
        f'<div class="foot">Erstellt mit netprobe {_e(snapshot.get("netprobe_version"))} · '
        f'lokale Netzwerk-/WAN-Monitoring-Appliance · keine Cloud-Dienste.</div>'
    )

    body = "\n".join(parts)
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f"<title>{_e(report.title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )
