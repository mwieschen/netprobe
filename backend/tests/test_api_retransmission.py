"""API-Smoke-Tests für TCP-Retransmission-Monitoring (mit gemockter Aufnahme/
tshark-Analyse, analog zu test_reports_integration.py)."""

from __future__ import annotations

import pytest

from app.services import retransmission_service as rs
from app.services.proc import ProcResult

_TSHARK_OUTPUT = (
    "1.0\t10.0.0.5\t8.8.8.8\t51000\t443\t\t1400\n"
    "2.0\t10.0.0.5\t8.8.8.8\t51000\t443\t1\t1400\n"
)


@pytest.fixture
def mocked_capture(monkeypatch, tmp_path):
    fake_pcap = tmp_path / "api_fake.pcapng"
    fake_pcap.write_bytes(b"\x0a\x0d\x0d\x0a")
    monkeypatch.setattr(rs.capture_service, "run_capture_window", lambda *a, **k: fake_pcap)
    monkeypatch.setattr(rs, "_local_ipv4", lambda iface: "10.0.0.5")
    monkeypatch.setattr(
        rs, "run_sync", lambda *a, **k: ProcResult(returncode=0, stdout=_TSHARK_OUTPUT, stderr="")
    )
    return fake_pcap


def test_retransmission_requires_auth(client):
    resp = client.post("/api/retransmission/tests", json={"interface": "eth0", "duration_s": 5})
    assert resp.status_code == 401


def test_retransmission_start_rejects_bad_interface(admin):
    resp = admin.post("/api/retransmission/tests", json={"interface": "eth0; rm -rf /", "duration_s": 5})
    assert resp.status_code == 400


def test_retransmission_config(admin):
    resp = admin.get("/api/retransmission/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "warn_threshold_pct" in body
    assert "alert_threshold_pct" in body


def test_retransmission_lifecycle(admin, mocked_capture):
    resp = admin.post("/api/retransmission/tests", json={"interface": "eth0", "duration_s": 5})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["total_packets"] == 2
    assert body["total_retransmissions"] == 1
    assert body["classification"] == "REMOTE_SERVER_ISSUE"
    assert len(body["targets"]) == 1
    assert body["targets"][0]["target_ip"] == "8.8.8.8"
    test_id = body["id"]

    hist = admin.get("/api/retransmission/tests?days=30")
    assert hist.status_code == 200
    assert any(t["id"] == test_id for t in hist.json())

    detail = admin.get(f"/api/retransmission/tests/{test_id}")
    assert detail.status_code == 200
    assert detail.json()["timeline"]

    corr = admin.get(f"/api/retransmission/tests/{test_id}/correlation")
    assert corr.status_code == 200
    assert "correlated" in corr.json()

    rpt = admin.post(f"/api/retransmission/tests/{test_id}/report", json={"customer": "ACME"})
    assert rpt.status_code == 201, rpt.text
    rpt_body = rpt.json()
    assert "report_id" in rpt_body

    dl = admin.get(rpt_body["html_url"])
    assert dl.status_code == 200
    assert "TCP-Retransmissions" in dl.text
    assert admin.delete(f"/api/reports/{rpt_body['report_id']}").status_code == 204

    assert admin.delete(f"/api/retransmission/tests/{test_id}").status_code == 204
    assert admin.get(f"/api/retransmission/tests/{test_id}").status_code == 404


def test_retransmission_report_404_for_missing_test(admin):
    resp = admin.post("/api/retransmission/tests/999999/report", json={})
    assert resp.status_code == 404


def test_retransmission_active_requires_targets(admin):
    resp = admin.post(
        "/api/retransmission/tests", json={"interface": "eth0", "duration_s": 5, "mode": "active"}
    )
    assert resp.status_code == 400


def test_retransmission_active_rejects_udp(admin):
    resp = admin.post(
        "/api/retransmission/tests",
        json={
            "interface": "eth0", "duration_s": 5, "mode": "active",
            "targets": [{"host": "1.1.1.1", "port": 443, "protocol": "udp"}],
        },
    )
    assert resp.status_code == 422  # Pydantic-Validierung (pattern "^tcp$")


@pytest.fixture
def mocked_active_capture(monkeypatch, tmp_path):
    fake_pcap = tmp_path / "active_fake.pcapng"
    fake_pcap.write_bytes(b"\x0a\x0d\x0d\x0a")
    monkeypatch.setattr(rs.capture_service, "run_capture_window", lambda *a, **k: fake_pcap)
    monkeypatch.setattr(rs, "_local_ipv4", lambda iface: "10.0.0.5")
    monkeypatch.setattr(rs, "resolve_active_target", lambda host, port, protocol="tcp": (host, port))
    monkeypatch.setattr(rs, "ensure_tool", lambda binary: binary)
    monkeypatch.setattr(rs, "_probe_loop", lambda ip, port, duration_s, interval_s=0.5: rs._ConnStats(
        attempts=3, errors=0, latencies_ms=[12.0, 18.0],
    ))
    monkeypatch.setattr(
        rs, "run_sync", lambda *a, **k: ProcResult(returncode=0, stdout=_TSHARK_OUTPUT, stderr="")
    )
    return fake_pcap


def test_retransmission_active_test_lifecycle(admin, mocked_active_capture):
    resp = admin.post(
        "/api/retransmission/tests",
        json={
            "interface": "eth0", "duration_s": 5, "mode": "active", "concurrent": True,
            "targets": [{"host": "8.8.8.8", "port": 443, "protocol": "tcp"}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["test_mode"] == "active"
    assert body["configured_targets"] == [{"host": "8.8.8.8", "ip": "8.8.8.8", "port": 443}]
    assert body["targets"][0]["connection_attempts"] == 3
    assert body["targets"][0]["avg_connect_latency_ms"] == 15.0
    assert admin.delete(f"/api/retransmission/tests/{body['id']}").status_code == 204


def test_retransmission_target_config_crud(admin):
    resp = admin.post(
        "/api/retransmission/targets",
        json={"name": "Cloudflare DNS", "host": "1.1.1.1", "port": 443, "description": "Uplink-Test"},
    )
    assert resp.status_code == 201, resp.text
    cfg = resp.json()
    assert cfg["usage_count"] == 0

    listed = admin.get("/api/retransmission/targets")
    assert listed.status_code == 200
    assert any(c["id"] == cfg["id"] for c in listed.json())

    assert admin.delete(f"/api/retransmission/targets/{cfg['id']}").status_code == 204
    assert not any(c["id"] == cfg["id"] for c in admin.get("/api/retransmission/targets").json())


def test_retransmission_target_config_requires_admin(client):
    assert client.get("/api/retransmission/targets").status_code == 401
    assert client.post("/api/retransmission/targets", json={}).status_code == 401


def test_retransmission_target_config_rejects_bad_host(admin):
    resp = admin.post(
        "/api/retransmission/targets",
        json={"name": "Böses Ziel", "host": "1.1.1.1; rm -rf /", "port": 443},
    )
    assert resp.status_code == 400


def test_retransmission_active_test_bumps_target_config_usage(admin, mocked_active_capture):
    cfg = admin.post(
        "/api/retransmission/targets",
        json={"name": "Google DNS", "host": "8.8.8.8", "port": 443},
    ).json()

    resp = admin.post(
        "/api/retransmission/tests",
        json={
            "interface": "eth0", "duration_s": 5, "mode": "active",
            "targets": [{"host": "8.8.8.8", "port": 443, "protocol": "tcp"}],
        },
    )
    assert resp.status_code == 201, resp.text

    updated = admin.get("/api/retransmission/targets").json()
    match = next(c for c in updated if c["id"] == cfg["id"])
    assert match["usage_count"] == 1
    assert match["last_used"] is not None

    admin.delete(f"/api/retransmission/tests/{resp.json()['id']}")
    admin.delete(f"/api/retransmission/targets/{cfg['id']}")
