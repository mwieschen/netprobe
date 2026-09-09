"""CLI-Unterbefehle für VoIP: einmaliger Verbindungstest und PCAP-Analyse.

Beides ruft dieselben Prüf-/Analysefunktionen wie die API.
"""

from __future__ import annotations

import argparse
import json as _json
from pathlib import Path

from app.db.session import session_scope
from app.models.voip import VoipServer
from app.services import voip_analysis, voip_service, wan_service
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

    analysis: dict | None = None
    if args.capture_interface:
        with session_scope() as db:
            srv = VoipServer(
                id=0, name="cli", host=host, sip_port=args.sip_port,
                sip_tls_port=args.port, sip_transport=args.transport, webrtc_ports="443,5001",
            )
            test = voip_service.run_test(
                db, srv, source="manual",
                capture_interface=args.capture_interface,
                capture_duration_s=args.capture_duration,
            )
            analysis = {
                "capture_path": test.capture_path,
                "sip": _json.loads(test.sip_analysis_json or "{}"),
                "codecs": _json.loads(test.codec_analysis_json or "{}"),
                "rtp": _json.loads(test.rtp_analysis_json or "{}"),
            }

    if args.json:
        print(_json.dumps(
            {"host": host, "sip_udp": sip_udp, "sip_tcp": sip_tcp, "tls": tls,
             "icmp": icmp, "analysis": analysis},
            indent=2, default=str,
        ))
        return

    print(f"VoIP-Test -> {host}")
    for name, r in (("sip_udp", sip_udp), ("sip_tcp", sip_tcp)):
        state = "erreichbar" if r["reachable"] else "keine Antwort"
        code = f" (SIP {r['status_code']})" if r["status_code"] else ""
        ms = f", {r['response_ms']} ms" if r["response_ms"] is not None else ""
        srv_hdr = (r.get("headers") or {}).get("server") or (r.get("headers") or {}).get("user-agent")
        extra = f"  [{srv_hdr}]" if srv_hdr else ""
        print(f"  {name:8s}: {state}{code}{ms}{extra}")
    if tls is not None:
        if tls["ok"]:
            verified = "verifiziert" if tls["verified"] else "NICHT verifiziert"
            print(f"  tls     : {tls['tls_version']} / {tls['tls_cipher']} · {verified} · "
                  f"Ablauf {tls['cert_expiry']}")
            print(f"            Subject {tls['cert_subject']} · Issuer {tls['cert_issuer']}")
        else:
            print(f"  tls     : fehlgeschlagen – {tls['error']}")
    if "error" in icmp:
        print(f"  icmp    : {icmp['error']}")
    else:
        print(f"  icmp    : Verlust {icmp['loss_pct']} %, RTT ø {icmp['rtt_avg']} ms")
    if analysis:
        _print_analysis(analysis["sip"], analysis["codecs"], analysis["rtp"], analysis["capture_path"])


def voip_analyze(args: argparse.Namespace) -> None:
    path = Path(args.capture_file)
    try:
        voip_analysis.validate_capture_path(path)
    except voip_analysis.VoipAnalysisError as exc:
        print(f"Fehler: {exc}")
        raise SystemExit(2) from exc
    res = voip_analysis.analyze_capture(path)
    if args.json:
        print(_json.dumps(res, indent=2, default=str))
        return
    if res["error"]:
        print(f"Hinweis: {res['error']}")
    _print_analysis(res["sip"], res["codecs"], res["rtp"], str(path))


def _print_analysis(sip: dict, codecs: dict, rtp: dict, capture_path: str | None) -> None:
    print(f"  PCAP    : {capture_path}")
    if sip:
        print(f"  SIP     : {sip.get('messages_captured', 0)} Nachrichten, "
              f"{sip.get('error_count', 0)} Fehler (4xx/5xx/6xx), "
              f"{sip.get('retransmissions', 0)} Retransmissions")
        for e in sip.get("errors", [])[:5]:
            print(f"            ! {e['code']} {e['reason']}")
    if codecs.get("codecs_offered"):
        bw = codecs.get("estimated_bandwidth_kbit")
        print(f"  Codecs  : {', '.join(codecs['codecs_offered'])}"
              + (f"  (~{bw} kbit/s)" if bw else "")
              + ("  [SRTP]" if codecs.get("secure") else ""))
    if rtp.get("streams_detected"):
        print(f"  RTP     : {rtp['streams_detected']} Stream(s), "
              f"Jitter ~{rtp.get('jitter_estimate_ms')} ms, "
              f"Verlust {rtp.get('packet_loss_percent')} %")
