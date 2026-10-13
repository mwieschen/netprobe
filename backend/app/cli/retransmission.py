"""CLI-Unterbefehle für TCP-Retransmission-Monitoring.

- ``retrans-test --interface eth0 --duration 60``  (passiv)
- ``retrans-test --interface eth0 --duration 60 --mode active --target 1.1.1.1:443``
- ``retrans-test --mode active --target-id 5``  (gespeichertes Ziel-Preset)
- ``retrans-report --test-id 5 --output report.html``
- ``retrans-monitor --interface eth0``  (Live-Schleife, Strg+C zum Beenden)
- ``retrans-target-add --name "Cloudflare DNS" --host 1.1.1.1 --port 443``
- ``retrans-target-list`` / ``retrans-target-delete --id 5``
"""

from __future__ import annotations

import argparse
import json as _json
import shutil

from app.db.session import session_scope
from app.models.retransmission import RetransmissionTargetConfig, RetransmissionTest
from app.services import capture_service, report_service, retransmission_service
from app.services.proc import validate_network_target


def _print_result(test: RetransmissionTest) -> None:
    if test.status != "done":
        print(f"Test #{test.id} fehlgeschlagen: {test.error}")
        return
    rate = test.retransmission_rate_percent or 0.0
    print(
        f"Test #{test.id}: {test.total_retransmissions}/{test.total_packets} Retransmissions "
        f"({rate:.2f} %) – {test.classification}"
    )
    if test.recommendation:
        print(f"  {test.recommendation}")
    for t in sorted(test.targets, key=lambda x: x.retransmissions, reverse=True)[:10]:
        kind = "LAN" if t.is_private else "WAN"
        conn = ""
        if t.connection_attempts is not None:
            conn = f", {t.connection_attempts} Verbindungen, {t.connection_errors} Fehler"
            if t.avg_connect_latency_ms is not None:
                conn += f", {t.avg_connect_latency_ms:.1f} ms"
        print(
            f"  {t.target_ip}:{t.target_port or '-'} [{kind}/{t.direction}] "
            f"{t.retransmissions} ({t.rate_percent:.2f} %){conn}"
        )


def _parse_host_port(raw: str) -> dict:
    host, _, port = raw.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"Ungültiges Ziel (erwartet HOST:PORT): {raw!r}")
    return {"host": host, "port": int(port), "protocol": "tcp"}


def _collect_targets(args: argparse.Namespace) -> list[dict] | None:
    targets: list[dict] = []
    if getattr(args, "target", None):
        targets.extend(_parse_host_port(t) for t in args.target)
    if getattr(args, "targets", None):
        targets.extend(_parse_host_port(t) for t in args.targets.split(",") if t.strip())
    if getattr(args, "target_id", None):
        with session_scope() as db:
            for cfg_id in args.target_id:
                cfg = db.get(RetransmissionTargetConfig, cfg_id)
                if cfg is None:
                    raise ValueError(f"Ziel-Preset #{cfg_id} nicht gefunden.")
                targets.append({"host": cfg.host, "port": cfg.port, "protocol": cfg.protocol})
    return targets or None


def retrans_test(args: argparse.Namespace) -> None:
    mode = getattr(args, "mode", "passive")
    try:
        targets = _collect_targets(args) if mode == "active" else None
    except ValueError as exc:
        print(str(exc))
        return
    with session_scope() as db:
        try:
            test = retransmission_service.run_test(
                db, interface=args.interface or capture_service.resolve_default_interface(),
                duration_s=args.duration, bpf=args.bpf,
                source="manual", mode=mode, targets=targets,
                concurrent=not getattr(args, "sequential", False),
            )
        except ValueError as exc:
            print(str(exc))
            return
        if args.json:
            print(_json.dumps(
                {
                    "id": test.id,
                    "status": test.status,
                    "total_packets": test.total_packets,
                    "total_retransmissions": test.total_retransmissions,
                    "retransmission_rate_percent": test.retransmission_rate_percent,
                    "classification": test.classification,
                    "recommendation": test.recommendation,
                    "error": test.error,
                },
                indent=2,
                default=str,
            ))
            return
        _print_result(test)


def retrans_report(args: argparse.Namespace) -> None:
    with session_scope() as db:
        test = db.get(RetransmissionTest, args.test_id)
        if test is None:
            print(f"Test #{args.test_id} nicht gefunden.")
            return
        report = report_service.generate(
            db, capture_id=None, job_id=None, retrans_test_id=test.id,
            customer="", location="", notes=None, sections=["summary", "retransmission"],
        )
        html_path, pdf_path = report_service.report_paths(report.id)
        src = pdf_path if (args.output.endswith(".pdf") and pdf_path.exists()) else html_path
        shutil.copyfile(src, args.output)
        print(f"Report #{report.id} geschrieben nach {args.output}")


def retrans_target_add(args: argparse.Namespace) -> None:
    try:
        host = validate_network_target(args.host)
    except ValueError as exc:
        print(str(exc))
        return
    with session_scope() as db:
        cfg = RetransmissionTargetConfig(
            name=args.name, host=host, port=args.port, protocol="tcp", description=args.description
        )
        db.add(cfg)
        db.flush()
        print(f"Ziel-Preset #{cfg.id} angelegt: {cfg.name} ({cfg.host}:{cfg.port})")


def retrans_target_list(args: argparse.Namespace) -> None:
    with session_scope() as db:
        rows = db.query(RetransmissionTargetConfig).order_by(RetransmissionTargetConfig.name).all()
        if not rows:
            print("Keine gespeicherten Ziel-Presets.")
            return
        for cfg in rows:
            used = f", zuletzt {cfg.last_used}" if cfg.last_used else ""
            print(f"  #{cfg.id} {cfg.name}: {cfg.host}:{cfg.port} (genutzt {cfg.usage_count}x{used})")


def retrans_target_delete(args: argparse.Namespace) -> None:
    with session_scope() as db:
        cfg = db.get(RetransmissionTargetConfig, args.id)
        if cfg is None:
            print(f"Ziel-Preset #{args.id} nicht gefunden.")
            return
        db.delete(cfg)
        print(f"Ziel-Preset #{args.id} gelöscht.")


def retrans_monitor(args: argparse.Namespace) -> None:
    print(f"Live-Monitoring auf {args.interface} (Strg+C zum Beenden) …")
    try:
        while True:
            with session_scope() as db:
                test = retransmission_service.run_test(
                    db, interface=args.interface, duration_s=args.duration, bpf=None, source="manual"
                )
                _print_result(test)
    except KeyboardInterrupt:
        print("\nBeendet.")
