"""CLI-Unterbefehle für die System-Health-Checks.

- ``dns-test --domain example.com [--server 8.8.8.8] [--type A]``
- ``health-check --all``  (führt alle konfigurierten Probes aus)
"""

from __future__ import annotations

import argparse
import json as _json

from app.db.session import session_scope
from app.models.health_check import HealthProbe
from app.services import health as health_framework


def dns_test(args: argparse.Namespace) -> None:
    servers = [args.server] if args.server else [""]
    outcome = health_framework.run_check(
        "dns", args.domain, {"record_type": args.type, "servers": servers}
    )
    if args.json:
        print(_json.dumps(outcome.__dict__, indent=2, default=str))
        return
    print(f"DNS {args.type} {args.domain}: {'OK' if outcome.ok else 'FEHLER'} – {outcome.status}")
    if outcome.error:
        print(f"  {outcome.error}")
    for label, info in (outcome.details.get("servers") or {}).items():
        print(f"  {label}: {info.get('answers') or info.get('error')}")


def health_check(args: argparse.Namespace) -> None:
    with session_scope() as db:
        probes = db.query(HealthProbe)
        if not args.all:
            probes = probes.filter(HealthProbe.enabled.is_(True))
        rows = probes.all()
        if not rows:
            print("Keine Health-Probes konfiguriert.")
            return
        for probe in rows:
            result = health_framework.run_probe(db, probe, source="manual")
            flag = "OK  " if result.ok else "FAIL"
            print(f"  [{flag}] {probe.check_type:5s} {probe.name:30s} {result.status or result.error or ''}")
