"""CLI-Unterbefehl ``traceroute`` – einmalige Pfad-Analyse mit optionaler Geo.

Der Lauf wird in der Datenbank gespeichert (wie über die API), damit er im UI
sichtbar ist und verglichen werden kann.
"""

from __future__ import annotations

import argparse
import json as _json

from app.db.session import session_scope
from app.services import traceroute_service
from app.services.proc import validate_network_target


def traceroute_cmd(args: argparse.Namespace) -> None:
    try:
        host = validate_network_target(args.target)
    except ValueError as exc:
        print(f"Fehler: {exc}")
        raise SystemExit(2) from exc

    with session_scope() as db:
        tr = traceroute_service.run(
            db,
            host=host,
            mode=args.mode,
            resolve_geo=args.geo,
            measure_mtu=args.mtu,
            source="manual",
        )
        hops = [
            {
                "hop": h.hop_number,
                "ip": h.ip,
                "hostname": h.hostname,
                "loss_pct": h.loss_pct,
                "latency_avg_ms": h.latency_avg_ms,
                "mtu": h.mtu,
                "country": h.country,
                "city": h.city,
                "asn": h.asn,
                "isp": h.isp,
            }
            for h in tr.hops
        ]
        summary = {
            "id": tr.id,
            "target": tr.target,
            "engine": tr.engine,
            "dest_reached": tr.dest_reached,
            "error": tr.error,
        }

    if args.json:
        print(_json.dumps({**summary, "hops": hops}, indent=2, default=str))
        return

    print(f"Traceroute #{summary['id']} -> {summary['target']} "
          f"(engine={summary['engine']}, Ziel erreicht={summary['dest_reached']})")
    if summary["error"]:
        print(f"  Fehler: {summary['error']}")
    for h in hops:
        geo = " / ".join(str(x) for x in (h["country"], h["city"], h["asn"]) if x)
        lat = f"{h['latency_avg_ms']} ms" if h["latency_avg_ms"] is not None else "*"
        loss = f"{h['loss_pct']}%" if h["loss_pct"] is not None else "?"
        mtu = f" MTU {h['mtu']}" if h["mtu"] else ""
        name = h["hostname"] or h["ip"] or "*"
        print(f"  {h['hop']:2d}  {name:40s}  {lat:>10s}  Loss {loss:>6s}{mtu}  {geo}")
