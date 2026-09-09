"""CLI-Unterbefehl ``voip-test`` – einmaliger VoIP-/3CX-Verbindungstest.

Ruft dieselben Prüffunktionen wie die API, ohne etwas zu persistieren.
"""

from __future__ import annotations

import argparse
import json as _json

from app.services import voip_service, wan_service
from app.services.proc import validate_network_target


def voip_test(args: argparse.Namespace) -> None:
    try:
        host = validate_network_target(args.server)
    except ValueError as exc:
        print(f"Fehler: {exc}")
        raise SystemExit(2) from exc

    sip_udp = voip_service.sip_options_ping(host, args.sip_port, "udp")
    sip_tcp = voip_service.sip_options_ping(host, args.sip_port, "tcp")
    tls = voip_service.check_tls(host, args.port) if args.port > 0 else None
    stats, ping_err = wan_service.measure(host, 5, 1000)
    icmp: dict = (
        {"loss_pct": stats.loss_pct, "rtt_avg": stats.rtt_avg} if stats else {"error": ping_err}
    )

    if args.json:
        print(_json.dumps(
            {"host": host, "sip_udp": sip_udp, "sip_tcp": sip_tcp, "tls": tls, "icmp": icmp},
            indent=2, default=str,
        ))
        return

    print(f"VoIP-Test -> {host}")
    for name, r in (("sip_udp", sip_udp), ("sip_tcp", sip_tcp)):
        state = "erreichbar" if r["reachable"] else "keine Antwort"
        code = f" (SIP {r['status_code']})" if r["status_code"] else ""
        ms = f", {r['response_ms']} ms" if r["response_ms"] is not None else ""
        print(f"  {name:8s}: {state}{code}{ms}")
    if tls is not None:
        if tls["ok"]:
            verified = "verifiziert" if tls["verified"] else "NICHT verifiziert"
            print(f"  tls     : Handshake {tls['handshake_ms']} ms, {verified}, "
                  f"Ablauf {tls['cert_expiry']}")
        else:
            print(f"  tls     : fehlgeschlagen – {tls['error']}")
    if "error" in icmp:
        print(f"  icmp    : {icmp['error']}")
    else:
        print(f"  icmp    : Verlust {icmp['loss_pct']} %, RTT ø {icmp['rtt_avg']} ms")
