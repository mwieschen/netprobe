"""Kommandozeilen-Werkzeug für Erst-Setup, Administration und Ad-hoc-Tests.

Beispiele::

    python -m app.cli init-db
    python -m app.cli create-admin --username admin
    python -m app.cli ensure-admin        # nutzt NETPROBE_ADMIN_USER/PASSWORD
    python -m app.cli voip-test --server 3cx.example.com --port 5061
    python -m app.cli retrans-test --interface eth0 --duration 60

Der Entry-Point ``app.cli:main`` (siehe ``pyproject.toml``) bleibt gültig.
"""

from __future__ import annotations

import argparse

from app.cli import admin, diagnostics, health, retransmission, scheduler, traceroute, voip


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

    p_rt = sub.add_parser("retrans-test", help="Einmaligen TCP-Retransmission-Test ausführen")
    p_rt.add_argument("--interface", default=None,
                       help="Capture-Interface (Standard: NETPROBE_RETRANS_DEFAULT_INTERFACE bzw. "
                            "Interface der Default-Route)")
    p_rt.add_argument("--duration", type=int, default=60, help="Mitschnitt-Dauer in Sekunden")
    p_rt.add_argument("--bpf", help="Optionaler BPF-Filter (nur Modus 'passive', Standard: 'tcp')")
    p_rt.add_argument("--mode", choices=("passive", "active"), default="passive")
    p_rt.add_argument("--target", action="append", metavar="HOST:PORT",
                       help="Aktives Ziel (mehrfach angebbar). Nur Modus 'active'.")
    p_rt.add_argument("--targets", metavar="HOST:PORT,HOST:PORT,...",
                       help="Kommagetrennte Liste aktiver Ziele. Nur Modus 'active'.")
    p_rt.add_argument("--target-id", type=int, action="append", metavar="ID",
                       help="Gespeichertes Ziel-Preset (mehrfach angebbar). Nur Modus 'active'.")
    p_rt.add_argument("--sequential", action="store_true",
                       help="Ziele nacheinander statt parallel testen (nur Modus 'active')")
    p_rt.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_rr = sub.add_parser("retrans-report", help="Report für einen Test exportieren")
    p_rr.add_argument("--test-id", type=int, required=True)
    p_rr.add_argument("--output", default="report.html", help="Zieldatei (.html oder .pdf)")

    p_rm = sub.add_parser("retrans-monitor", help="Live-Monitoring (Strg+C zum Beenden)")
    p_rm.add_argument("--interface", required=True, help="Capture-Interface (z. B. eth0)")
    p_rm.add_argument("--duration", type=int, default=30, help="Dauer je Messintervall in Sekunden")

    p_rta = sub.add_parser("retrans-target-add", help="Ziel-Preset für aktive Tests speichern")
    p_rta.add_argument("--name", required=True)
    p_rta.add_argument("--host", required=True, help="IP oder Hostname")
    p_rta.add_argument("--port", type=int, required=True)
    p_rta.add_argument("--description", default=None)

    sub.add_parser("retrans-target-list", help="Gespeicherte Ziel-Presets anzeigen")

    p_rtd = sub.add_parser("retrans-target-delete", help="Ziel-Preset löschen")
    p_rtd.add_argument("--id", type=int, required=True)

    sub.add_parser("scheduler-status", help="Status der globalen Automatisierung anzeigen")
    sub.add_parser("scheduler-enable", help="Globale Automatisierung aktivieren")
    sub.add_parser("scheduler-disable", help="Globale Automatisierung pausieren")

    p_dr = sub.add_parser("diagnostics-run", help="Mehrere Tests gebündelt zu mehreren Zielen ausführen")
    p_dr.add_argument("--interface", default=None,
                       help="Capture-Interface (für retrans/broadcast)")
    p_dr.add_argument("--duration", type=int, default=60, help="Dauer in Sekunden")
    p_dr.add_argument("--target", action="append", metavar="[Name:]Host",
                       help="Test-Ziel (mehrfach angebbar), z. B. Cloudflare:1.1.1.1 oder nur 1.1.1.1")
    p_dr.add_argument("--tests", metavar="ping,retrans,mtu,broadcast,traceroute,speedtest",
                       help="Kommagetrennte Liste (Standard: ping)")
    p_dr.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")

    p_dpa = sub.add_parser("diagnostics-preset-add", help="Ziel-/Test-Auswahl als Preset speichern")
    p_dpa.add_argument("--name", required=True)
    p_dpa.add_argument("--interface", default=None)
    p_dpa.add_argument("--duration", type=int, default=60)
    p_dpa.add_argument("--target", action="append", metavar="[Name:]Host")
    p_dpa.add_argument("--tests", metavar="ping,retrans,mtu,broadcast,traceroute,speedtest")

    sub.add_parser("diagnostics-preset-list", help="Gespeicherte Presets anzeigen")

    p_dpd = sub.add_parser("diagnostics-preset-delete", help="Preset löschen")
    p_dpd.add_argument("--id", type=int, required=True)

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
    elif args.command == "retrans-test":
        retransmission.retrans_test(args)
    elif args.command == "retrans-report":
        retransmission.retrans_report(args)
    elif args.command == "retrans-monitor":
        retransmission.retrans_monitor(args)
    elif args.command == "retrans-target-add":
        retransmission.retrans_target_add(args)
    elif args.command == "retrans-target-list":
        retransmission.retrans_target_list(args)
    elif args.command == "retrans-target-delete":
        retransmission.retrans_target_delete(args)
    elif args.command == "scheduler-status":
        scheduler.scheduler_status(args)
    elif args.command == "scheduler-enable":
        scheduler.scheduler_enable(args)
    elif args.command == "scheduler-disable":
        scheduler.scheduler_disable(args)
    elif args.command == "diagnostics-run":
        diagnostics.diagnostics_run(args)
    elif args.command == "diagnostics-preset-add":
        diagnostics.diagnostics_preset_add(args)
    elif args.command == "diagnostics-preset-list":
        diagnostics.diagnostics_preset_list(args)
    elif args.command == "diagnostics-preset-delete":
        diagnostics.diagnostics_preset_delete(args)


if __name__ == "__main__":
    main()
