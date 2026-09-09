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


def test_voip_server_crud(admin):
    resp = admin.post(
        "/api/voip/servers",
        json={"name": "3CX Zentrale", "host": "pbx.example.com", "sip_tls_port": 5061},
    )
    assert resp.status_code == 201, resp.text
    server_id = resp.json()["id"]
    assert resp.json()["last_test"] is None

    resp = admin.get("/api/voip/servers")
    assert any(s["id"] == server_id for s in resp.json())

    resp = admin.patch(f"/api/voip/servers/{server_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = admin.get(f"/api/voip/servers/{server_id}/history")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = admin.delete(f"/api/voip/servers/{server_id}")
    assert resp.status_code == 204


def test_voip_test_run_unreachable_host(admin):
    resp = admin.post(
        "/api/voip/servers",
        json={"name": "tot", "host": "127.0.0.1", "sip_port": 9, "sip_tls_port": 0,
              "webrtc_ports": "9"},
    )
    server_id = resp.json()["id"]

    resp = admin.post(f"/api/voip/servers/{server_id}/test", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["server_id"] == server_id
    assert isinstance(body["webrtc_results"], list)

    resp = admin.get(f"/api/voip/servers/{server_id}/history")
    assert len(resp.json()) == 1

    admin.delete(f"/api/voip/servers/{server_id}")


def test_voip_server_rejects_bad_host_on_test(admin):
    resp = admin.post(
        "/api/voip/servers",
        json={"name": "x", "host": "-x", "sip_tls_port": 0, "webrtc_ports": ""},
    )
    server_id = resp.json()["id"]
    resp = admin.post(f"/api/voip/servers/{server_id}/test", json={})
    assert resp.status_code == 201
    assert resp.json()["ok"] is False
    assert "Ungültiger Host" in (resp.json()["error"] or "")
    admin.delete(f"/api/voip/servers/{server_id}")


def test_voip_servers_require_auth(client):
    assert client.get("/api/voip/servers").status_code == 401
    assert client.post("/api/voip/servers", json={"name": "x", "host": "y"}).status_code == 401


def test_traceroute_target_crud(admin):
    resp = admin.post(
        "/api/traceroute/targets",
        json={"name": "Google DNS", "host": "8.8.8.8", "mode": "icmp"},
    )
    assert resp.status_code == 201, resp.text
    target_id = resp.json()["id"]

    resp = admin.get("/api/traceroute/targets")
    assert any(t["id"] == target_id for t in resp.json())

    resp = admin.patch(f"/api/traceroute/targets/{target_id}", json={"enabled": False, "queries": 5})
    assert resp.status_code == 200
    assert resp.json()["queries"] == 5

    resp = admin.delete(f"/api/traceroute/targets/{target_id}")
    assert resp.status_code == 204


def test_traceroute_run_without_tool(admin):
    # In der Testumgebung ist weder mtr noch traceroute vorhanden -> Lauf wird
    # persistiert, completed=False, error gesetzt, aber sauberes 201.
    resp = admin.post("/api/traceroute/run", json={"host": "8.8.8.8", "resolve_geo": False})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["target"] == "8.8.8.8"
    assert isinstance(body["hops"], list)
    assert "id" in body

    resp = admin.get("/api/traceroute/history")
    assert any(t["id"] == body["id"] for t in resp.json())

    resp = admin.get(f"/api/traceroute/{body['id']}")
    assert resp.status_code == 200


def test_traceroute_run_rejects_bad_host(admin):
    resp = admin.post("/api/traceroute/run", json={"host": "-x", "resolve_geo": False})
    assert resp.status_code == 201
    assert "Ungültiger Host" in (resp.json()["error"] or "")


def test_traceroute_compare_missing(admin):
    assert admin.get("/api/traceroute/compare?a=99998&b=99999").status_code == 404


def test_health_check_types(admin):
    resp = admin.get("/api/health-checks/types")
    assert resp.status_code == 200
    assert set(resp.json()) == {"dns", "http", "smtp", "imap", "ntp"}


def test_health_probe_crud_and_run(admin):
    resp = admin.post(
        "/api/health-checks",
        json={
            "name": "DNS Cloudflare", "check_type": "dns", "target": "example.com",
            "params": {"record_type": "A", "servers": ["127.0.0.1"]},
            "interval_s": 300,
        },
    )
    assert resp.status_code == 201, resp.text
    probe_id = resp.json()["id"]
    assert resp.json()["params"]["record_type"] == "A"
    assert resp.json()["last_result"] is None

    resp = admin.patch(f"/api/health-checks/{probe_id}", json={"params": {"record_type": "AAAA"}})
    assert resp.json()["params"]["record_type"] == "AAAA"

    # Lauf: 127.0.0.1:53 antwortet in der Testumgebung nicht -> ok=False, kein Crash
    resp = admin.post(f"/api/health-checks/{probe_id}/run")
    assert resp.status_code == 201, resp.text
    assert resp.json()["probe_id"] == probe_id

    resp = admin.get(f"/api/health-checks/{probe_id}/history")
    assert len(resp.json()) == 1

    resp = admin.get("/api/health-checks/overview")
    assert resp.json()["total"] >= 1

    assert admin.delete(f"/api/health-checks/{probe_id}").status_code == 204


def test_health_probe_rejects_bad_type(admin):
    resp = admin.post(
        "/api/health-checks",
        json={"name": "x", "check_type": "telnet", "target": "y"},
    )
    assert resp.status_code == 422


def test_openapi_available(client):
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "netprobe API"
