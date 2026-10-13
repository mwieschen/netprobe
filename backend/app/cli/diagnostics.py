"""CLI-Unterbefehle für Network Diagnostics.

- ``diagnostics-run --interface eth0 --duration 60 --target Cloudflare:1.1.1.1
  --target Google:8.8.8.8 --tests ping,retrans,mtu,traceroute``
- ``diagnostics-preset-add``/``-list``/``-delete``
"""

from __future__ import annotations

import argparse
import json as _json

from app.db.session import session_scope
from app.models.diagnostics import DiagnosticPreset
from app.services import capture_service, diagnostics_service
from app.services.diagnostics_service import TEST_TYPES, DiagnosticsError


def _parse_target(raw: str) -> dict:
    name, _, host = raw.partition(":")
    if not host:
        name, host = host, name  # nur Host angegeben, kein "Name:Host"
    return {"name": name or host, "host": host}


def _parse_tests(raw: str | None) -> dict:
    if not raw:
        return {"ping": True}
    selected = {t.strip() for t in raw.split(",") if t.strip()}
    unknown = selected - set(TEST_TYPES)
    if unknown:
        raise ValueError(f"Unbekannte Tests: {', '.join(sorted(unknown))} (gültig: {', '.join(TEST_TYPES)})")
    return {t: (t in selected) for t in TEST_TYPES}


def diagnostics_run(args: argparse.Namespace) -> None:
    targets = [_parse_target(t) for t in (args.target or [])]
    try:
        tests = _parse_tests(args.tests)
    except ValueError as exc:
        print(str(exc))
        return

    try:
        run = diagnostics_service.run_diagnostics(
            targets=targets, tests=tests, duration_s=args.duration,
            interface=args.interface or capture_service.resolve_default_interface(),
        )
    except DiagnosticsError as exc:
        print(str(exc))
        return

    if args.json:
        print(_json.dumps({
            "id": run.id, "status": run.status, "overall_status": run.overall_status,
            "results": _json.loads(run.results_json) if run.results_json else None,
            "error": run.error,
        }, indent=2, default=str))
        return

    if run.status != "done":
        print(f"Lauf #{run.id} fehlgeschlagen: {run.error}")
        return

    results = _json.loads(run.results_json) if run.results_json else {}
    print(f"Lauf #{run.id}: Gesamtstatus {run.overall_status}")
    for t in results.get("targets", []):
        issues = f" ({', '.join(t['issues'])})" if t["issues"] else ""
        print(f"  {t['name']} ({t['host']}): {t['status']}{issues}")
    if results.get("broadcast"):
        b = results["broadcast"]
        if b.get("frame_distribution"):
            print(f"  Broadcast: {b['frame_distribution']['broadcast_pct']} %")
    if results.get("speedtest"):
        s = results["speedtest"]
        print(f"  Speedtest: {s.get('download_mbps')} / {s.get('upload_mbps')} Mbit/s")


def diagnostics_preset_add(args: argparse.Namespace) -> None:
    targets = [_parse_target(t) for t in (args.target or [])]
    try:
        tests = _parse_tests(args.tests)
    except ValueError as exc:
        print(str(exc))
        return
    with session_scope() as db:
        preset = DiagnosticPreset(
            name=args.name, targets_json=_json.dumps(targets), tests_json=_json.dumps(tests),
            interface=args.interface or capture_service.resolve_default_interface(),
            duration_s=args.duration,
        )
        db.add(preset)
        db.flush()
        print(f"Preset #{preset.id} angelegt: {preset.name}")


def diagnostics_preset_list(args: argparse.Namespace) -> None:
    with session_scope() as db:
        rows = db.query(DiagnosticPreset).order_by(DiagnosticPreset.name).all()
        if not rows:
            print("Keine gespeicherten Presets.")
            return
        for p in rows:
            tests = _json.loads(p.tests_json)
            active = ",".join(k for k, v in tests.items() if v)
            print(f"  #{p.id} {p.name}: {p.interface}, {p.duration_s}s, Tests: {active}")


def diagnostics_preset_delete(args: argparse.Namespace) -> None:
    with session_scope() as db:
        preset = db.get(DiagnosticPreset, args.id)
        if preset is None:
            print(f"Preset #{args.id} nicht gefunden.")
            return
        db.delete(preset)
        print(f"Preset #{args.id} gelöscht.")
