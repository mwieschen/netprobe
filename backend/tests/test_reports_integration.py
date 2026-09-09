"""End-to-End-Test der Report-Erzeugung mit gemockter tshark-Analyse."""

from __future__ import annotations

import pytest

_ANALYSIS = {
    "packet_count": 500,
    "packets_analyzed": 500,
    "sampled": False,
    "frame_distribution": {
        "total": 500, "unicast": 400, "broadcast": 80, "multicast": 20, "other": 0,
        "unicast_pct": 80.0, "broadcast_pct": 16.0, "multicast_pct": 4.0, "other_pct": 0.0,
    },
    "top_protocols": [{"protocol": "TCP", "packets": 300, "bytes": 250000}],
    "top_conversations": [
        {"addr_a": "10.0.0.1", "addr_b": "10.0.0.2", "packets": 200, "bytes": 200000}
    ],
    "tcp_health": {
        "tcp_packets": 300, "retransmissions": 20, "retransmission_pct": 6.7,
        "dup_acks": 5, "dup_ack_pct": 1.7, "resets": 2,
    },
    "warnings": [{"level": "warn", "code": "high_broadcast", "message": "Hoher Broadcast-Anteil: 16.0 %."}],
    "protocol_hierarchy": [{"level": 0, "protocol": "eth", "frames": 500, "bytes": 400000}],
    "top_endpoints": [], "tcp_conversations": [], "udp_conversations": [],
    "dns_queries": [], "tls_sni": [], "tcp_retransmissions": 20, "tcp_resets": 2,
    "expert_infos": [],
}


@pytest.fixture
def capture_with_file():
    from app.core.config import settings
    from app.db.session import session_scope
    from app.models.capture import Capture

    settings.capture_dir.mkdir(parents=True, exist_ok=True)
    pcap = settings.capture_dir / "capture_test.pcapng"
    pcap.write_bytes(b"\x0a\x0d\x0d\x0a")  # PCAPNG-Magie, Inhalt egal (Analyse gemockt)

    with session_scope() as db:
        cap = Capture(iface="eth0", status="done", duration_s=10, path=str(pcap),
                      size_bytes=pcap.stat().st_size, packet_count=500)
        db.add(cap)
        db.flush()
        cap_id = cap.id
    return cap_id


def test_report_end_to_end(admin, monkeypatch, capture_with_file):
    from app.services import report_service

    monkeypatch.setattr(report_service.capture_service, "analyze", lambda *a, **k: dict(_ANALYSIS))

    sections = ["summary", "ping", "speedtest", "iperf3", "mtu", "passive"]
    resp = admin.post(
        "/api/reports",
        json={
            "capture_id": capture_with_file,
            "customer": "ACME",
            "location": "Berlin",
            "notes": "Testlauf",
            "sections": sections,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "netprobe-Report – ACME"
    assert body["sections"] == sections
    assert body["html_url"].endswith("/download?fmt=html")
    # Key Metrics werden mitgeliefert
    labels = {m["label"] for m in body["key_metrics"]}
    assert "Broadcast-Anteil" in labels
    report_id = body["id"]

    # Preview-Endpoint liefert dieselben Key Metrics ohne Datei
    prev = admin.post("/api/reports/preview", json={"capture_id": capture_with_file, "sections": sections})
    assert prev.status_code == 200
    assert {m["label"] for m in prev.json()["key_metrics"]} == labels

    # Metadaten
    assert admin.get(f"/api/reports/{report_id}").json()["customer"] == "ACME"

    # HTML-Download
    dl = admin.get(f"/api/reports/{report_id}/download?fmt=html")
    assert dl.status_code == 200
    assert "text/html" in dl.headers["content-type"]
    assert "netprobe-Report" in dl.text
    assert "Passive Analyse" in dl.text
    assert "Hoher Broadcast-Anteil" in dl.text

    # PDF ist optional; ohne WeasyPrint -> 404 + Hinweis im error-Feld
    if not body["has_pdf"]:
        assert admin.get(f"/api/reports/{report_id}/download?fmt=pdf").status_code == 404

    # Aufräumen
    assert admin.delete(f"/api/reports/{report_id}").status_code == 204
