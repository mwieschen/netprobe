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


def test_capture_job_lifecycle(admin):
    # dumpcap fehlt in der Testumgebung -> Job landet in status "error", aber die
    # CRUD-Endpunkte müssen sauber funktionieren.
    resp = admin.post(
        "/api/capture-jobs",
        json={"name": "lo", "iface": "lo", "max_file_size_mb": 10, "max_files": 5},
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    assert resp.json()["max_files"] == 5

    resp = admin.get("/api/capture-jobs")
    assert any(j["id"] == job_id for j in resp.json())

    resp = admin.get(f"/api/capture-jobs/{job_id}/captures")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = admin.post(f"/api/capture-jobs/{job_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"

    resp = admin.delete(f"/api/capture-jobs/{job_id}")
    assert resp.status_code == 204


def test_capture_job_rejects_bad_iface(admin):
    resp = admin.post("/api/capture-jobs", json={"iface": "-x; rm -rf /"})
    assert resp.status_code == 400


def test_capture_analysis_endpoints_404(admin):
    # Analyse-Endpoints: unbekannte Capture -> 404, Route ist verdrahtet.
    for suffix in ("analysis", "packets", "conversations", "stats"):
        resp = admin.get(f"/api/captures/99999/{suffix}")
        assert resp.status_code == 404, (suffix, resp.text)


def test_iperf3_server_status_and_client(admin):
    resp = admin.get("/api/iperf3/server/status")
    assert resp.status_code == 200
    assert resp.json()["running"] is False

    # iperf3 fehlt in der Testumgebung -> Ergebnis ok=False, aber 201 + persistiert.
    resp = admin.post("/api/iperf3/client", json={"host": "127.0.0.1", "duration_s": 1})
    assert resp.status_code == 201, resp.text
    assert resp.json()["ok"] is False
    assert resp.json()["host"] == "127.0.0.1"

    resp = admin.get("/api/iperf3/results")
    assert any(r["host"] == "127.0.0.1" for r in resp.json())


def test_iperf3_client_rejects_bad_host(admin):
    resp = admin.post("/api/iperf3/client", json={"host": "-R"})
    assert resp.status_code == 201
    assert resp.json()["ok"] is False
    assert "Ungültiger Host" in (resp.json()["error"] or "")


def test_mtu_test_rejects_bad_target(admin):
    resp = admin.post("/api/mtu-test", json={"target": "-s 9000", "min_mtu": 576, "max_mtu": 1500})
    assert resp.status_code == 400


def test_reports_crud_and_validation(admin):
    assert admin.get("/api/reports").json() == []

    resp = admin.post("/api/reports", json={"customer": "Test"})
    assert resp.status_code == 400  # weder capture_id noch job_id

    resp = admin.post("/api/reports", json={"capture_id": 99999})
    assert resp.status_code == 400  # Capture existiert nicht

    assert admin.get("/api/reports/99999").status_code == 404


def test_openapi_available(client):
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "netprobe API"
