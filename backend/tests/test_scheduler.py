"""Tests für die globale Scheduler-Übersicht und den Automatisierungs-Schalter."""

from __future__ import annotations


def test_scheduler_status_requires_auth(client):
    assert client.get("/api/scheduler/status").status_code == 401


def test_scheduler_enable_disable_requires_admin(client):
    assert client.post("/api/scheduler/disable").status_code == 401
    assert client.post("/api/scheduler/enable").status_code == 401


def test_scheduler_status_always_lists_global_jobs(admin):
    resp = admin.get("/api/scheduler/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    ids = {j["id"] for j in body["jobs"]}
    assert "speedtest" in ids
    assert "retransmission_monitor" in ids


def test_scheduler_wan_target_appears_in_status(admin):
    resp = admin.post(
        "/api/wan/targets",
        json={
            "name": "Scheduler-Test-Ziel", "host": "1.1.1.1",
            "interval_s": 60, "count": 5, "timeout_ms": 1000,
        },
    )
    assert resp.status_code == 201, resp.text
    target_id = resp.json()["id"]
    try:
        status = admin.get("/api/scheduler/status").json()
        job = next(j for j in status["jobs"] if j["id"] == f"wan_target_{target_id}")
        assert job["type"] == "wan"
        assert job["label"] == "Scheduler-Test-Ziel"
        assert job["enabled"] is True
        assert job["interval_seconds"] == 60
    finally:
        admin.delete(f"/api/wan/targets/{target_id}")


def test_scheduler_enable_disable_pauses_and_resumes_jobs(admin):
    try:
        resp = admin.post("/api/scheduler/disable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        speedtest_job = next(j for j in body["jobs"] if j["id"] == "speedtest")
        assert speedtest_job["paused"] is True

        resp = admin.post("/api/scheduler/enable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        speedtest_job = next(j for j in body["jobs"] if j["id"] == "speedtest")
        assert speedtest_job["paused"] is False
    finally:
        # Globalen Schalter für nachfolgende Tests wieder aktiv lassen.
        admin.post("/api/scheduler/enable")


def test_settings_retransmission_fields_live_reload_scheduler(admin):
    resp = admin.get("/api/settings")
    assert resp.status_code == 200
    original = resp.json()
    try:
        resp = admin.patch(
            "/api/settings",
            json={"retrans_enabled": True, "retrans_scheduled_interval_min": 15},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["retrans_enabled"] is True
        assert body["retrans_scheduled_interval_min"] == 15

        status = admin.get("/api/scheduler/status").json()
        job = next(j for j in status["jobs"] if j["id"] == "retransmission_monitor")
        assert job["enabled"] is True
        assert job["interval_seconds"] == 15 * 60

        resp = admin.patch("/api/settings", json={"retrans_enabled": False})
        status = admin.get("/api/scheduler/status").json()
        job = next(j for j in status["jobs"] if j["id"] == "retransmission_monitor")
        assert job["enabled"] is False
    finally:
        admin.patch(
            "/api/settings",
            json={
                "retrans_enabled": original["retrans_enabled"],
                "retrans_scheduled_interval_min": original["retrans_scheduled_interval_min"],
            },
        )
