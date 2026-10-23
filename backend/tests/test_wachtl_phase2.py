"""Wachtl-Clients Phase 2: Störungsmeldungen, Client-Hinweise, Ausfallanalyse, Diagnose-Auslöser."""

from __future__ import annotations

import time

import pytest

from app.services.wachtl_analysis import detect_incidents
from app.services.wachtl_events import EventError, validate_findings_batch, validate_report
from app.services.wachtl_service import start_diagnostics as real_start_diagnostics

NOW = 1_760_620_800


# --- Validierung ------------------------------------------------------------
def test_validate_report():
    v = validate_report({"report_id": "ABC-1", "occurred_at": NOW - 60, "category": "slow",
                         "description": "lahm\x00 ", "user": "KUNDE\\max"}, NOW)
    assert v["report_id"] == "abc-1" and v["duration"] == "unknown" and v["description"] == "lahm"
    for bad in ({}, {"report_id": "x", "occurred_at": NOW, "category": "nope"},
                {"report_id": "x", "occurred_at": NOW - 8 * 86400, "category": "slow"},
                {"report_id": "x y", "occurred_at": NOW, "category": "slow"}):
        with pytest.raises(EventError):
            validate_report(bad, NOW)


def test_validate_findings_batch_skips_invalid():
    ok = {"finding_id": "f1", "kind": "cable_unplugged", "severity": "critical", "state": "active",
          "detected_at": NOW - 10, "details": {"adapter": "Ethernet"}}
    items, errors = validate_findings_batch({"findings": [ok, {"finding_id": "f2", "kind": "X"}]}, NOW)
    assert len(items) == 1 and items[0]["title"] == "Netzwerkkabel getrennt"
    assert len(errors) == 1 and errors[0].startswith("Eintrag 2")
    with pytest.raises(EventError):
        validate_findings_batch({"findings": [ok] * 51}, NOW)


# --- Ausfallanalyse ---------------------------------------------------------
GW, DNS1, DNS2, SRV = 1, 2, 3, 4
ROLES = {GW: "gateway", DNS1: "reference", DNS2: "reference", SRV: "target"}
M0 = NOW - NOW % 60


def _ok(probe: int, minute: int, *skip: int) -> dict:
    return {(probe, c, minute): (12, 0) for c in ROLES if c not in skip}


def _bad(probe: int, minute: int, *checks: int) -> dict:
    return {(probe, c, minute): (12, 12) for c in checks}


def test_incident_causes_single_probe():
    b: dict = {}
    for i in range(10):
        b.update(_ok(10, M0 + i * 60))
    b.update(_bad(10, M0 + 60, GW))                 # Minute 1: Gateway → LAN
    b.update(_bad(10, M0 + 180, DNS1, DNS2))        # Minute 3+4: alle Referenzen → WAN
    b.update(_bad(10, M0 + 240, DNS1, DNS2))
    b.update(_bad(10, M0 + 360, SRV))               # Minute 6: nur Ziel
    res = detect_incidents(1, [10], ROLES, b, threshold_pct=25.0, min_interval_s=5, merge_gap_min=0)
    got = {(i["cause"], i["check_id"], i["start"] - M0, i["minutes"]) for i in res}
    assert got == {("lan", None, 60, 1), ("wan", None, 180, 2), ("target", SRV, 360, 1)}
    assert all(i["scope"] == "probe" for i in res)


def test_incident_flapping_merged_into_episode():
    """Schwankender Verlust (WAN/Ziel abwechselnd, mit sauberer Minute dazwischen) = eine Störung."""
    b: dict = {}
    for i in range(8):
        b.update(_ok(10, M0 + i * 60))
    b.update(_bad(10, M0, DNS1, DNS2))
    b.update(_bad(10, M0 + 60, SRV))
    b.update(_bad(10, M0 + 180, DNS1, DNS2))
    b.update(_bad(10, M0 + 240, DNS1))
    res = detect_incidents(1, [10], ROLES, b, threshold_pct=25.0, min_interval_s=5)
    assert len(res) == 1
    assert (res[0]["cause"], res[0]["minutes"], res[0]["affected_minutes"]) == ("wan", 5, 4)


def test_incident_site_fold_and_gap():
    b: dict = {}
    for p in (10, 11):
        for i in range(8):
            b.update(_ok(p, M0 + i * 60))
        b.update(_bad(p, M0 + 120, DNS1, DNS2))      # beide PCs: WAN → Standort-Störung
    b.update(_bad(11, M0 + 300, SRV))                # nur PC 11: Ziel
    for c in ROLES:                                  # PC 10: Lücke Minute 4–6
        for i in (4, 5, 6):
            b.pop((10, c, M0 + i * 60))
    res = detect_incidents(1, [10, 11], ROLES, b, threshold_pct=25.0, min_interval_s=5)
    site = [i for i in res if i["scope"] == "site"]
    assert len(site) == 1 and site[0]["cause"] == "wan" and site[0]["probe_ids"] == [10, 11]
    probe = {(i["cause"], tuple(i["probe_ids"]), i["start"] - M0, i["minutes"])
             for i in res if i["scope"] == "probe"}
    assert probe == {("probe", (10,), 240, 3), ("target", (11,), 300, 1)}


def test_incident_threshold_and_short_gap():
    b: dict = {}
    for i in range(5):
        b.update(_ok(10, M0 + i * 60))
    b[(10, GW, M0 + 60)] = (12, 2)                   # 17 % < 25 % → kein Ausfall
    for c in ROLES:                                  # eine fehlende Minute ist noch keine Lücke
        b.pop((10, c, M0 + 180))
    assert detect_incidents(1, [10], ROLES, b, threshold_pct=25.0, min_interval_s=5) == []


# --- Probe-API --------------------------------------------------------------
@pytest.fixture
def probe(admin, tmp_path, monkeypatch):
    from app.api.wachtl_probe import enroll_rate_limiter
    from app.services import wachtl_service

    enroll_rate_limiter._hits.clear()
    calls: list[dict] = []

    def fake_start(probe_id, *, report_id=None, automatic=False):
        calls.append({"probe_id": probe_id, "report_id": report_id, "automatic": automatic})

    monkeypatch.setattr(wachtl_service, "start_diagnostics", fake_start)
    admin.put("/api/clients/settings", json={"enabled": True})
    site = admin.post("/api/clients/sites", json={"name": f"P2-{time.time_ns()}"}).json()
    checks = {}
    for name, target, role in (("GW", "gateway", "gateway"), ("DNS", "8.8.8.8", "reference")):
        checks[role] = admin.post("/api/clients/checks", json={
            "name": name, "target": target, "role": role, "site_id": site["id"]}).json()["id"]
    tok = admin.post("/api/clients/tokens", json={"site_id": site["id"]}).json()["token"]
    e = admin.post("/api/probe/v1/enroll", json={"enrollment_token": tok, "hostname": "PC-P2"}).json()
    return {"c": admin, "site": site, "checks": checks, "id": e["probe_id"],
            "auth": {"Authorization": f"Bearer {e['token']}"}, "calls": calls}


def test_config_enables_client_findings(probe):
    cfg = probe["c"].get("/api/probe/v1/config", headers=probe["auth"]).json()
    assert cfg["client_findings"] == {"enabled": True, "notify": True}


def test_report_idempotent_and_triggers_diagnose(probe):
    c, auth = probe["c"], probe["auth"]
    body = {"report_id": "11111111-2222-3333-4444-555555555555", "occurred_at": int(time.time()) - 120,
            "category": "no_connection", "duration": "minutes", "description": "Internet weg",
            "user": "KUNDE\\max", "hostname": "PC-P2"}
    r1 = c.post("/api/probe/v1/report", json=body, headers=auth)
    assert r1.status_code == 201
    r2 = c.post("/api/probe/v1/report", json=body, headers=auth)
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"]
    assert probe["calls"] == [{"probe_id": probe["id"], "report_id": r1.json()["id"], "automatic": True}]
    bad = c.post("/api/probe/v1/report", json={**body, "category": "x"}, headers=auth)
    assert bad.status_code == 400 and "category" in bad.json()["error"]

    reports = c.get(f"/api/clients/reports?probe_id={probe['id']}").json()
    assert len(reports) == 1 and reports[0]["category_label"].startswith("Keine Verbindung")
    done = c.post(f"/api/clients/reports/{r1.json()['id']}/status", json={"status": "done", "note": "Router"})
    assert done.json()["status"] == "done" and done.json()["done_by"] == "admin"
    assert c.get(f"/api/clients/reports?status=open&probe_id={probe['id']}").json() == []


def test_findings_upsert_and_restart(probe):
    c, auth = probe["c"], probe["auth"]
    now = int(time.time())
    active = {"finding_id": "nic-1", "kind": "cable_unplugged", "severity": "critical", "state": "active",
              "detected_at": now - 300, "title": "Netzwerkkabel getrennt", "solution": "Kabel prüfen",
              "adapter": "Ethernet"}
    r = c.post("/api/probe/v1/findings", json={"findings": [active, {"kind": "x"}]}, headers=auth).json()
    assert (r["stored"], r["new"], r["rejected"]) == (1, 1, 1)
    resolved = {**active, "state": "resolved", "resolved_at": now - 60, "message": "Wieder verbunden",
                "details": {"problem": "Netzwerkkabel getrennt"}}
    c.post("/api/probe/v1/findings", json={"findings": [resolved]}, headers=auth)
    # Ältere Nachlieferung macht den Hinweis nicht wieder aktiv
    c.post("/api/probe/v1/findings", json={"findings": [active]}, headers=auth)
    f = c.get(f"/api/clients/findings?probe_id={probe['id']}").json()
    assert len(f) == 1 and f[0]["state"] == "resolved" and f[0]["solution"] == "Kabel prüfen"
    assert f[0]["details"]["resolution"] == "Wieder verbunden"

    # Dienst-Neustart (uptime kleiner als zuvor) schließt alte aktive Hinweise
    c.post("/api/probe/v1/findings", json={"findings": [{**active, "finding_id": "nic-2"}]}, headers=auth)
    c.post("/api/probe/v1/heartbeat", json={"uptime_s": 5000}, headers=auth)
    c.post("/api/probe/v1/heartbeat", json={"uptime_s": 30}, headers=auth)
    f = {x["kind"] + x["state"] for x in c.get(f"/api/clients/findings?probe_id={probe['id']}").json()}
    assert f == {"cable_unpluggedresolved"}


def test_incidents_endpoint(probe):
    c, auth, chk = probe["c"], probe["auth"], probe["checks"]
    now_ms = int(time.time() * 1000)
    start = now_ms - now_ms % 60000 - 6 * 60000
    lines = []
    for minute in range(6):
        for s in range(0, 60, 5):
            ts = start + minute * 60000 + s * 1000
            gw_up = 0 if minute == 2 else 1
            lines.append(f'wachtl_ping_up{{check_id="{chk["gateway"]}"}} {gw_up} {ts}')
            lines.append(f'wachtl_ping_up{{check_id="{chk["reference"]}"}} {gw_up} {ts}')
    r = c.post("/api/probe/v1/ingest", content="\n".join(lines), headers={**auth, "X-Batch-Id": "inc-1"})
    assert r.status_code == 204
    inc = c.get(f"/api/clients/incidents?range=1h&site_id={probe['site']['id']}").json()
    assert len(inc) == 1
    assert inc[0]["cause"] == "lan" and inc[0]["minutes"] == 1 and inc[0]["probe_hostnames"] == ["PC-P2"]
    assert inc[0]["max_loss_pct"] == 100.0 and inc[0]["ongoing"] is False


def test_manual_diagnose(probe):
    c = probe["c"]
    r = c.post(f"/api/clients/probes/{probe['id']}/diagnose")
    assert r.status_code == 202 and r.json()["targets"][-1] == "8.8.8.8"
    assert probe["calls"][-1] == {"probe_id": probe["id"], "report_id": None, "automatic": False}


def test_diagnose_targets_and_cooldown(probe, monkeypatch):
    """Echte start_diagnostics-Logik: Ziele ohne gateway, PC-IP vorn, Sperrzeit für automatische Läufe."""
    from app.db.session import session_scope
    from app.models.wachtl import WachtlProbe
    from app.services import wachtl_service as real

    started: list = []
    monkeypatch.setattr(real, "_diagnose_job", lambda *a: started.append(a))

    class _T:
        def __init__(self, target, args, **_kw):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(real.threading, "Thread", _T)
    with session_scope() as db:
        p = db.get(WachtlProbe, probe["id"])
        p.last_ip = "203.0.113.7"                       # NAT-Adresse des Standorts
        p.addresses_json = '["169.254.3.3", "192.168.10.20"]'
    real._diag_last.clear()
    real_start_diagnostics(probe["id"], automatic=True)
    assert [t["host"] for t in started[0][1]] == ["192.168.10.20", "8.8.8.8"]
    with pytest.raises(real.DiagnoseBusy):
        real_start_diagnostics(probe["id"], automatic=True)
    real_start_diagnostics(probe["id"])        # manuell: keine Sperrzeit
    assert len(started) == 2
