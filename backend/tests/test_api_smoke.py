"""Integrations-Smoke-Tests für die API."""

from __future__ import annotations


def test_health_public(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_requires_auth(client):
    resp = client.get("/api/speedtest/latest")
    assert resp.status_code == 401


def test_login_and_me(admin):
    resp = admin.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "falsch"})
    assert resp.status_code in (401, 429)


def test_wan_target_crud(admin):
    resp = admin.post(
        "/api/wan/targets",
        json={"name": "Cloudflare", "host": "1.1.1.1", "interval_s": 60, "count": 5, "timeout_ms": 1000},
    )
    assert resp.status_code == 201, resp.text
    target_id = resp.json()["id"]

    resp = admin.get("/api/wan/targets")
    assert any(t["id"] == target_id for t in resp.json())

    resp = admin.patch(f"/api/wan/targets/{target_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = admin.delete(f"/api/wan/targets/{target_id}")
    assert resp.status_code == 204


def test_ping_job_lifecycle(admin):
    resp = admin.post("/api/ping/jobs", json={"name": "Gateway", "host": "127.0.0.1", "interval_s": 5})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]

    resp = admin.post(f"/api/ping/jobs/{job_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    resp = admin.get(f"/api/ping/jobs/{job_id}/stats")
    assert resp.status_code == 200

    resp = admin.delete(f"/api/ping/jobs/{job_id}")
    assert resp.status_code == 204


def test_dashboard_health(admin):
    resp = admin.get("/api/dashboard/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "tools" in body
    assert "disk_free_mb" in body


def test_settings_admin_only(client, admin):
    resp = admin.get("/api/settings")
    assert resp.status_code == 200
    assert "speedtest_interval_min" in resp.json()


def test_openapi_available(client):
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "netprobe API"
