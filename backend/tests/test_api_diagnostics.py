"""API-Smoke-Tests für Network Diagnostics (Sub-Tests gemockt, analog zu
test_api_retransmission.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import diagnostics_service as ds
from app.services.parsers import RttStats


@pytest.fixture
def mocked_subtests(monkeypatch):
    monkeypatch.setattr(ds.wan_service, "measure", lambda host, count, timeout_ms: (
        RttStats(loss_pct=0.0, rtt_min=9.0, rtt_avg=10.0, rtt_max=11.0, jitter_ms=1.0, rtts=[10.0]), None
    ))


def test_diagnostics_requires_auth(client):
    resp = client.post("/api/diagnostics/run", json={"interface": "eth0", "targets": []})
    assert resp.status_code == 401


def test_diagnostics_run_rejects_missing_targets(admin, mocked_subtests):
    resp = admin.post(
        "/api/diagnostics/run",
        json={"interface": "eth0", "duration_s": 10, "targets": [],
              "tests": {"ping": True}},
    )
    assert resp.status_code == 400


def test_diagnostics_run_rejects_bad_interface(admin, mocked_subtests):
    resp = admin.post(
        "/api/diagnostics/run",
        json={
            "interface": "eth0; rm -rf /", "duration_s": 10,
            "targets": [{"name": "x", "host": "1.1.1.1"}], "tests": {"ping": True},
        },
    )
    assert resp.status_code == 400


def test_diagnostics_run_lifecycle(admin, mocked_subtests):
    resp = admin.post(
        "/api/diagnostics/run",
        json={
            "interface": "eth0", "duration_s": 10,
            "targets": [{"name": "Cloudflare", "host": "1.1.1.1"}],
            "tests": {"ping": True},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["overall_status"] == "OK"
    assert body["results"]["targets"][0]["ping"]["loss_pct"] == 0.0
    run_id = body["id"]

    hist = admin.get("/api/diagnostics/runs?days=30")
    assert hist.status_code == 200
    assert any(r["id"] == run_id for r in hist.json())

    detail = admin.get(f"/api/diagnostics/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["results"] is not None

    assert admin.delete(f"/api/diagnostics/runs/{run_id}").status_code == 204
    assert admin.get(f"/api/diagnostics/runs/{run_id}").status_code == 404


def test_diagnostics_preset_crud(admin):
    resp = admin.post(
        "/api/diagnostics/presets",
        json={
            "name": "WAN-Check", "interface": "eth0", "duration_s": 60,
            "targets": [{"name": "Cloudflare", "host": "1.1.1.1"}],
            "tests": {"ping": True, "retrans": True},
        },
    )
    assert resp.status_code == 201, resp.text
    preset = resp.json()
    assert preset["tests"]["retrans"] is True

    listed = admin.get("/api/diagnostics/presets")
    assert listed.status_code == 200
    assert any(p["id"] == preset["id"] for p in listed.json())

    assert admin.delete(f"/api/diagnostics/presets/{preset['id']}").status_code == 204
    assert not any(p["id"] == preset["id"] for p in admin.get("/api/diagnostics/presets").json())


def test_diagnostics_preset_requires_admin(client):
    assert client.get("/api/diagnostics/presets").status_code == 401
    assert client.post("/api/diagnostics/presets", json={}).status_code == 401


def test_diagnostics_run_with_retrans_combines_targets(admin, mocked_subtests, monkeypatch):
    fake_target = SimpleNamespace(target_ip="1.1.1.1", rate_percent=0.0, retransmissions=0,
                                   packets=5, connection_errors=0)
    fake_test = SimpleNamespace(
        id=7, status="done", classification="OK", retransmission_rate_percent=0.0,
        error=None, targets=[fake_target],
    )
    monkeypatch.setattr(ds.retransmission_service, "run_test", lambda db, **kw: fake_test)
    monkeypatch.setattr(
        ds.retransmission_service, "resolve_active_target",
        lambda host, port, protocol="tcp": (host, port),
    )

    resp = admin.post(
        "/api/diagnostics/run",
        json={
            "interface": "eth0", "duration_s": 10,
            "targets": [{"name": "Cloudflare", "host": "1.1.1.1"}],
            "tests": {"ping": False, "retrans": True},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["results"]["retrans_test_id"] == 7
    assert body["results"]["targets"][0]["retrans"]["rate_percent"] == 0.0
    admin.delete(f"/api/diagnostics/runs/{body['id']}")
