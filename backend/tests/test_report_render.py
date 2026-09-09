"""Unit-Tests für das Report-HTML-Template (ohne tshark/DB)."""

from __future__ import annotations

from app.models.report import Report
from app.services.report_service import (
    _build_interpretations,
    _build_key_metrics,
    render_report_html,
)


def _passive() -> dict:
    return {
        "packet_count": 1234, "packets_analyzed": 1234, "sampled": False,
        "frame_distribution": {
            "total": 1234, "unicast": 1000, "broadcast": 200, "multicast": 34, "other": 0,
            "unicast_pct": 81.0, "broadcast_pct": 16.2, "multicast_pct": 2.8, "other_pct": 0.0,
        },
        "tcp_health": {
            "tcp_packets": 900, "retransmissions": 45, "retransmission_pct": 5.0,
            "dup_acks": 10, "dup_ack_pct": 1.1, "resets": 3,
        },
        "warnings": [
            {"level": "warn", "code": "high_broadcast", "message": "Hoher Broadcast-Anteil: 16.2 %."}
        ],
        "top_protocols": [{"protocol": "TCP", "packets": 900, "bytes": 800000}],
        "top_conversations": [{"addr_a": "10.0.0.1", "addr_b": "10.0.0.2", "packets": 400, "bytes": 500000}],
        "protocol_hierarchy": [{"level": 0, "protocol": "eth", "frames": 1234, "bytes": 900000}],
        "dns_queries": [], "tls_sni": [], "expert_infos": [],
    }


def _active() -> dict:
    return {
        "ping": [{"name": "Gateway", "host": "1.1.1.1", "status": "running", "count": 1000,
                  "loss": 20, "loss_pct": 2.0, "rtt_min": 5.0, "rtt_avg": 8.0, "rtt_max": 30.0,
                  "jitter_ms": 3.0}],
        "wan": [],
        "speedtest": {"ts": "2026-01-01 12:00:00", "ok": True, "download_mbps": 94.2,
                      "upload_mbps": 40.1, "ping_ms": 12.0, "jitter_ms": 2.1, "packet_loss": 0.0,
                      "server_name": "Test", "error": None},
        "iperf_tcp": [{"ts": "2026-01-01 12:00:00", "ok": True, "host": "10.0.0.2", "port": 5201,
                       "reverse": False, "parallel": 1, "duration_s": 10, "sent_mbps": 940.0,
                       "recv_mbps": 935.0, "jitter_ms": None, "lost_percent": None,
                       "lost_packets": None, "packets": None, "out_of_order": None,
                       "retransmits": 12, "error": None}],
        "iperf_udp": [{"ts": "2026-01-01 12:00:00", "ok": True, "host": "10.0.0.2", "port": 5201,
                       "reverse": False, "parallel": 1, "duration_s": 10, "sent_mbps": 50.0,
                       "recv_mbps": None, "jitter_ms": 0.8, "lost_percent": 2.5,
                       "lost_packets": 25, "packets": 1000, "out_of_order": 4,
                       "retransmits": None, "error": None}],
        "mtu": [{"ts": "2026-01-01 12:00:00", "target": "1.1.1.1", "ok": True, "path_mtu": 1492,
                 "min_mtu": 576, "max_mtu": 1500, "probe_count": 8, "error": None}],
    }


def _snapshot() -> dict:
    passive, active = _passive(), _active()
    return {
        "generated": "2026-01-01 12:00:00",
        "netprobe_version": "1.0.0",
        "source": {"kind": "Capture", "ref": "#3", "iface": "eth0",
                   "ts": "2026-01-01 12:00:00", "size": "1,0 MB", "bpf": "–"},
        "sections": ["summary", "ping", "speedtest", "iperf3", "mtu", "passive"],
        "key_metrics": _build_key_metrics(passive, active),
        "interpretations": _build_interpretations(passive, active),
        "active": active,
        "passive": passive,
    }


def test_key_metrics_and_interpretations():
    snap = _snapshot()
    labels = {m["label"] for m in snap["key_metrics"]}
    assert {"Paketverlust Ping", "UDP-Verlust (iperf3)", "TCP-Retransmissions",
            "Broadcast-Anteil", "Speedtest-Download"} == labels
    # Ping-Verlust 2 % > 1 % -> warn
    ping_metric = next(m for m in snap["key_metrics"] if m["label"] == "Paketverlust Ping")
    assert ping_metric["level"] == "warn" and ping_metric["value"] == "2,0 %"
    # UDP-Verlust 2,5 % -> warn-Interpretation vorhanden
    assert any("UDP-Paketverlust" in i["text"] and i["level"] == "warn" for i in snap["interpretations"])
    assert any("out-of-order" in i["text"] for i in snap["interpretations"])
    assert any("Pfad-MTU zu 1.1.1.1: 1492" in i["text"] for i in snap["interpretations"])


def test_render_report_html_smoke():
    report = Report(
        id=7, title="netprobe-Report – ACME <script>", customer="ACME <script>",
        location="Berlin", notes="Zeile 1\nZeile 2", sections="[]", data_json="{}", has_pdf=False,
    )
    out = render_report_html(report, _snapshot())
    assert out.startswith("<!doctype html>")
    assert "Zusammenfassung (Key Metrics)" in out
    assert "Passive Analyse (PCAP)" in out
    assert "iperf3 (TCP/UDP)" in out
    assert "Out-of-Order" in out
    assert "Zeile 1<br>Zeile 2" in out
    # HTML-Escaping
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
