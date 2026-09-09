"""Kommandozeilen-Werkzeug für Erst-Setup, Administration und Ad-hoc-Tests.

Beispiele::

    python -m app.cli init-db
    python -m app.cli create-admin --username admin
    python -m app.cli ensure-admin        # nutzt NETPROBE_ADMIN_USER/PASSWORD
    python -m app.cli voip-test --server 3cx.example.com --port 5061

Der Entry-Point ``app.cli:main`` (siehe ``pyproject.toml``) bleibt gültig.
"""

from __future__ import annotations

import argparse

from app.cli import admin, health, traceroute, voip


def main() -> None:
    parser = argparse.ArgumentParser(prog="netprobe", description="netprobe CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Datenbank-Schema erstellen")

    p_admin = sub.add_parser("create-admin", help="Admin-Benutzer anlegen/aktualisieren")
    p_admin.add_argument("--username")
    p_admin.add_argument("--password", help="Nicht empfohlen (erscheint in Prozessliste)")
    p_admin.add_argument("--force", action="store_true", help="Bestehenden Benutzer überschreiben")

    sub.add_parser("ensure-admin", help="Admin aus Env-Variablen anlegen (nur wenn leer)")

    p_voip = sub.add_parser("voip-test", help="Einmaligen VoIP-/3CX-Verbindungstest ausführen")
    p_voip.add_argument("--server", required=True, help="FQDN oder IP der 3CX-Anlage")
    p_voip.add_argument("--port", type=int, default=5061, help="SIP-TLS-Port (Standard 5061)")
    p_voip.add_argument("--sip-port", type=int, default=5060, help="SIP-Port UDP/TCP (Standard 5060)")
    p_voip.add_argument(
        "--transport", choices=("udp", "tcp"), default="udp", help="Transport für den OPTIONS-Ping"
    )
    p_voip.add_argument("--capture-interface", help="Interface für einen parallelen PCAP-Mitschnitt")
    p_voip.add_argument("--capture-duration", type=int, default=10, help="Mitschnitt-Dauer (s)")
    p_voip.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_va = sub.add_parser("voip-analyze", help="Vorhandene PCAP auf SIP/SDP/RTP analysieren")
    p_va.add_argument("--capture-file", required=True, help="Pfad zur .pcap/.pcapng (unter capture_dir)")
    p_va.add_argument("--json", action="store_true")

    p_tr = sub.add_parser("traceroute", help="Einmalige Pfad-Analyse (mtr/traceroute)")
    p_tr.add_argument("--target", required=True, help="FQDN oder IP des Ziels")
    p_tr.add_argument("--mode", choices=("icmp", "udp", "tcp"), default="icmp")
    p_tr.add_argument("--geo", action="store_true", help="Geo-/ASN-Auflösung der Hops")
    p_tr.add_argument("--mtu", action="store_true", help="Pfad-MTU zum Ziel messen")
    p_tr.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_dns = sub.add_parser("dns-test", help="DNS-Auflösung testen")
    p_dns.add_argument("--domain", required=True)
    p_dns.add_argument("--server", help="DNS-Server (Standard: System-Resolver)")
    p_dns.add_argument("--type", default="A", help="Record-Typ (A, AAAA, MX, TXT, …)")
    p_dns.add_argument("--json", action="store_true")

    p_hc = sub.add_parser("health-check", help="Konfigurierte Health-Probes ausführen")
    p_hc.add_argument("--all", action="store_true", help="auch deaktivierte Probes")

    args = parser.parse_args()
    if args.command == "init-db":
        admin.init_db()
    elif args.command == "create-admin":
        admin.create_admin(args)
    elif args.command == "ensure-admin":
        admin.ensure_admin(args)
    elif args.command == "voip-test":
        voip.voip_test(args)
    elif args.command == "voip-analyze":
        voip.voip_analyze(args)
    elif args.command == "traceroute":
        traceroute.traceroute_cmd(args)
    elif args.command == "dns-test":
        health.dns_test(args)
    elif args.command == "health-check":
        health.health_check(args)


if __name__ == "__main__":
    main()
