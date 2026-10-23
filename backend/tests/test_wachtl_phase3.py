"""Wachtl-Clients Phase 3: Check-Typen, Pfad/MTR, Bandbreite, Sprache, RTP-Reflektor, Aufträge, Ereignisse."""

from __future__ import annotations

import struct
import time

import pytest

from app.services import wachtl_checktypes as ct
from app.services import wachtl_results as wr
from app.services.rtp_reflector import Reflector, parse


# --- Check-Typen ------------------------------------------------------------
def test_parse_params_defaults_and_hidden_fields():
    p = ct.parse_params("iperf", {"peer": "server", "host": "ignoriert", "direction": "down"})
    assert p["host"] == "" and p["port"] == 5201 and p["direction"] == "down" and p["schedule_min"] == 60
    p = ct.parse_params("speedtest", {"method": "http", "protocol": "udp"})
    assert p["protocol"] == "tcp"  # nur bei method=iperf sichtbar
    assert ct.parse_params("path", {"retrace_on_loss": False})["retrace_on_loss"] is False


@pytest.mark.parametrize(
    ("ctype", "raw", "field"),
    [
        ("iperf", {"peer": "host"}, "host"),
        ("iperf", {"duration_s": 60, "timeout_s": 60}, "timeout_s"),
        ("rtp", {"weekdays": "189"}, "weekdays"),
        ("rtp", {"codec": "opus"}, "codec"),
        ("speedtest", {"method": "iperf"}, "host"),
        ("iperf", {"peer": "probe", "peer_probe_id": 99}, "peer_probe_id"),
        ("path", {"max_hops": 99}, "max_hops"),
        ("iperf", {"peer": "host", "host": "-oops"}, "host"),
    ],
)
def test_parse_params_errors(ctype, raw, field):
    with pytest.raises(ct.ParamError) as exc:
        ct.parse_params(ctype, raw, probe_ids={1})
    assert exc.value.field == field


def test_target_summary_offset():
    p = ct.parse_params("rtp", {})
    assert ct.target_for("rtp", p) == "netprobe" and ct.timeout_ms("rtp", p) == 40000
    assert ct.summary("rtp", 60, p) == "G.711, 20 ms, 30 s, alle 15 min"
    assert ct.target_for("speedtest", ct.parse_params("speedtest", {})) == "speed.cloudflare.com"
    assert 0 <= ct.schedule_offset(3, 7, 60, -1) < 60 and ct.schedule_offset(3, 7, 60, 75) == 15


# --- Pfad / MTR -------------------------------------------------------------
def _hop(hop, ip, count, loss, rtt):
    return {"hop": hop, "ip": ip, "count": count, "loss": loss, "rtt_avg": rtt}


def test_evaluate_hops_icmp_limit_loss_latency():
    rows = [
        _hop(1, "192.168.1.1", 100, 0, 1.0),
        _hop(2, "10.0.0.1", 100, 40, 8.0),  # Drosselung
        _hop(3, "62.1.1.1", 100, 0, 9.0),
        _hop(4, "62.1.1.2", 100, 0, 48.0),  # Latenzsprung
        _hop(5, "62.1.1.3", 100, 6, 50.0),
        _hop(6, "1.1.1.1", 100, 7, 51.0),
    ]  # Verlust ab Hop 5
    v = {r["hop"]: r["verdict"] for r in wr.evaluate_hops(rows)}
    assert v == {1: "ok", 2: "icmp_limit", 3: "ok", 4: "latency", 5: "loss", 6: "loss_continues"}


def test_path_differs_ignores_silent_hops():
    a = [{"hop": 1, "ip": "10.0.0.1"}, {"hop": 2, "ip": "62.1.1.1"}, {"hop": 3, "ip": "1.1.1.1"}]
    assert not wr.path_differs(
        a, [{"hop": 1, "ip": "10.0.0.1"}, {"hop": 2, "ip": None}, {"hop": 3, "ip": "1.1.1.1"}]
    )
    assert wr.path_differs(
        a, [{"hop": 1, "ip": "10.0.0.1"}, {"hop": 2, "ip": "62.9.9.9"}, {"hop": 3, "ip": "1.1.1.1"}]
    )
    filled = wr.fill_known(a, [{"hop": 2, "ip": None, "rtt_ms": None}])
    assert filled == [{"hop": 2, "ip": "62.1.1.1", "rtt_ms": None, "stale": True}]


# --- Bandbreite / Sprache ---------------------------------------------------
def test_bandwidth_validation_and_events():
    trigger, res = wr.validate_bandwidth(
        {
            "trigger": "manual",
            "results": [
                {"direction": "down", "mbps": 42.0},
                {"direction": "up", "mbps": None, "error": "Timeout"},
                {"direction": "sideways", "mbps": 1},
            ],
        }
    )
    assert trigger == "manual" and len(res) == 2
    events = wr.bandwidth_events("WAN", {"min_down_mbps": 50, "min_up_mbps": 0}, res)
    assert [e.kind for e in events] == ["bandwidth_low", "bandwidth_error"]
    with pytest.raises(wr.ResultError):
        wr.validate_bandwidth({"results": []})


def test_rtp_events():
    _, v = wr.validate_rtp({"result": {"mos": 2.9, "loss_pct": 8, "jitter_ms": 40, "rtt_ms": 120}})
    (e,) = wr.rtp_events("Telefonie", {"min_mos": 3.6}, v)
    assert (e.kind, e.severity) == ("mos_low", "critical") and "schlecht" in e.title
    _, ok = wr.validate_rtp({"result": {"mos": 4.3}})
    assert wr.rtp_events("Telefonie", {"min_mos": 3.6}, ok) == []


# --- RTP-Reflektor ----------------------------------------------------------
def _rtp(seq: int, ssrc: int = 0xABCD, magic: bytes = b"WTL1", size: int = 172) -> bytes:
    head = struct.pack(">BBHII", 0x80, 0, seq, seq * 160, ssrc)
    fields = struct.pack(">4sqqI", magic, 123456, 0, 0)
    return head + fields + b"\xff" * (size - len(head) - len(fields))


def test_reflector_reflects_same_size_with_counter():
    clock = [100.0]
    r = Reflector(clock=lambda: clock[0])
    out = [r.handle(_rtp(i), ("10.0.0.5", 4000)) for i in range(3)]
    assert all(o is not None and len(o) == 172 for o in out)
    _magic, t1, t2, count = struct.unpack_from(">4sqqI", out[2], 12)
    assert (t1, t2 > 0, count) == (123456, True, 3)
    query = r.handle(_rtp(99, magic=b"WTLQ"), ("10.0.0.5", 4000))
    assert query is not None and struct.unpack_from(">4sqqI", query, 12)[3] == 3  # Abfrage zählt nicht mit
    assert r.handle(b"GET / HTTP/1.1\r\n" * 4, ("10.0.0.6", 80)) is None
    assert parse(_rtp(1)[:20]) is None


def test_reflector_rate_limit_per_source():
    r = Reflector(clock=lambda: 50.0)  # Uhr steht: keine Nachfüllung
    answered = sum(r.handle(_rtp(i), ("10.0.0.7", 4000)) is not None for i in range(2100))
    assert answered == 2000 and r.dropped == 100


# --- API --------------------------------------------------------------------
@pytest.fixture
def env(admin):
    from app.api.wachtl_probe import enroll_rate_limiter

    enroll_rate_limiter._hits.clear()
    admin.put("/api/clients/settings", json={"enabled": True})
    site = admin.post("/api/clients/sites", json={"name": f"P3-{time.time_ns()}"}).json()
    tok = admin.post("/api/clients/tokens", json={"site_id": site["id"]}).json()["token"]
    probes = {}
    for host in ("PC-A", "PC-B"):
        e = admin.post("/api/probe/v1/enroll", json={"enrollment_token": tok, "hostname": host}).json()
        probes[host] = {"id": e["probe_id"], "auth": {"Authorization": f"Bearer {e['token']}"}}
    return {"c": admin, "site": site, "probes": probes}


def _check(c, site_id, **kw):
    r = c.post("/api/clients/checks", json={"site_id": site_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_check_types_endpoint(env):
    types = {t["key"]: t for t in env["c"].get("/api/clients/check-types").json()}
    assert set(types) == {"ping", "path", "iperf", "speedtest", "rtp"}
    field = next(f for f in types["iperf"]["fields"] if f["key"] == "host")
    assert field["show_if"] == ["peer", ["host"]]


def test_config_peers(env):
    c, site, probes = env["c"], env["site"]["id"], env["probes"]
    iperf = _check(c, site, name="iperf Zentrale", type="iperf", params={"peer": "server"})
    assert iperf["target"] == "netprobe" and iperf["interval_s"] == 60 and iperf["timeout_ms"] == 60000
    rtp_probe = _check(
        c,
        site,
        name="Sprache zu PC-B",
        type="rtp",
        params={"peer": "probe", "peer_probe_id": probes["PC-B"]["id"]},
    )
    assert rtp_probe["target"] == "probe:PC-B"
    rtp_host = _check(
        c, site, name="Sprache 3CX", type="rtp", params={"peer": "host", "host": "pbx.example.com"}
    )

    cfg = c.get("/api/probe/v1/config", headers=probes["PC-A"]["auth"]).json()
    by_id = {x["id"]: x for x in cfg["checks"]}
    assert by_id[iperf["id"]]["peer"]["error"].startswith("iperf3-Server auf netprobe ist aus")
    assert 0 <= by_id[iperf["id"]]["params"]["offset_min"] < 60
    assert "nicht als Gegenstelle freigegeben" in by_id[rtp_probe["id"]]["peer"]["error"]
    assert by_id[rtp_host["id"]]["peer"] == {"host": "pbx.example.com", "port": 5004, "error": None}

    # PC-B als Gegenstelle freigeben + Adresse per Heartbeat → Peer gesetzt, ETag ändert sich
    c.patch(f"/api/clients/probes/{probes['PC-B']['id']}", json={"peer_enabled": True})
    c.post("/api/probe/v1/heartbeat", json={"addresses": ["192.168.5.20"]}, headers=probes["PC-B"]["auth"])
    cfg2 = c.get("/api/probe/v1/config", headers=probes["PC-A"]["auth"]).json()
    assert cfg2["etag"] != cfg["etag"]
    assert {x["id"]: x for x in cfg2["checks"]}[rtp_probe["id"]]["peer"] == {
        "host": "192.168.5.20",
        "port": 5004,
        "error": None,
    }
    cfg_b = c.get("/api/probe/v1/config", headers=probes["PC-B"]["auth"]).json()
    assert cfg_b["iperf_server"] == {"enabled": True, "port": 5201} and cfg_b["rtp_reflector"]["enabled"]


def test_path_results_hops_and_events(env):
    c, site, pa = env["c"], env["site"]["id"], env["probes"]["PC-A"]
    path = _check(c, site, name="Pfad Cloudflare", type="path", target="1.1.1.1", interval_s=5)
    hops = [
        {"hop": 1, "ip": "192.168.1.1", "rtt_ms": 1},
        {"hop": 2, "ip": "62.155.1.1", "rtt_ms": 9},
        {"hop": 3, "ip": "1.1.1.1", "rtt_ms": 11},
    ]
    body = {
        "check_id": path["id"],
        "ts": int(time.time()),
        "reached": True,
        "hops": hops,
        "reason": "schedule",
    }
    assert c.post("/api/probe/v1/results/path", json=body, headers=pa["auth"]).json() == {"changed": False}
    assert c.post("/api/probe/v1/results/path", json=body, headers=pa["auth"]).json() == {"changed": False}
    changed = {**body, "hops": [hops[0], {"hop": 2, "ip": "80.1.1.1", "rtt_ms": 15}, hops[2]]}
    assert c.post("/api/probe/v1/results/path", json=changed, headers=pa["auth"]).json() == {"changed": True}
    # fremder Check-Typ / fremde Check-ID
    assert (
        c.post(
            "/api/probe/v1/results/path", json={**body, "check_id": 999999}, headers=pa["auth"]
        ).status_code
        == 422
    )

    now_ms = int(time.time() * 1000)
    lines = []
    for i in range(12):
        ts = now_ms - i * 5000
        for hop, ip, up in ((1, "192.168.1.1", 1), (2, "80.1.1.1", 0 if i % 2 else 1), (3, "1.1.1.1", 1)):
            lines.append(f'wachtl_hop_up{{check_id="{path["id"]}",hop="{hop}",hop_ip="{ip}"}} {up} {ts}')
            if up:
                lines.append(
                    f'wachtl_hop_rtt_ms{{check_id="{path["id"]}",hop="{hop}",hop_ip="{ip}"}} {hop * 5} {ts}'
                )
    r = c.post(
        "/api/probe/v1/ingest", content="\n".join(lines), headers={**pa["auth"], "X-Batch-Id": "hops-1"}
    )
    assert r.status_code == 204

    view = c.get(f"/api/clients/probes/{pa['id']}/checks/{path['id']}/path?range=1h").json()
    assert view["current"]["hops"][1]["ip"] == "80.1.1.1" and len(view["history"]) == 2
    verdicts = {h["hop"]: h["verdict"] for h in view["hops"]}
    assert verdicts == {1: "ok", 2: "icmp_limit", 3: "ok"}
    events = c.get(f"/api/clients/events?probe_id={pa['id']}").json()
    assert [e["kind"] for e in events] == ["path_change"]


def test_bandwidth_rtp_matrix_and_commands(env):
    c, site, pa = env["c"], env["site"]["id"], env["probes"]["PC-A"]
    wan = _check(c, site, name="WAN", type="speedtest", params={"min_down_mbps": 100})
    rtp = _check(
        c, site, name="Telefonie", type="rtp", params={"peer": "host", "host": "10.0.0.9", "min_mos": 3.6}
    )
    r = c.post(
        "/api/probe/v1/results/bandwidth",
        headers=pa["auth"],
        json={
            "check_id": wan["id"],
            "ts": int(time.time()),
            "trigger": "schedule",
            "results": [
                {"direction": "down", "mbps": 48.5, "latency_ms": 12},
                {"direction": "up", "mbps": 19.2},
            ],
        },
    )
    assert r.status_code == 201 and r.json() == {"stored": 2}
    r = c.post(
        "/api/probe/v1/results/rtp",
        headers=pa["auth"],
        json={
            "check_id": rtp["id"],
            "ts": int(time.time()),
            "result": {
                "mos": 3.2,
                "r_factor": 62,
                "loss_pct": 4,
                "jitter_ms": 25,
                "rtt_ms": 80,
                "mode": "wachtl",
                "codec": "g711",
                "sent": 1500,
                "received": 1440,
            },
        },
    )
    assert r.status_code == 201

    runs = c.get(f"/api/clients/probes/{pa['id']}/checks/{wan['id']}/bandwidth").json()
    assert sorted(x["direction"] for x in runs) == ["down", "up"]
    assert c.get(f"/api/clients/probes/{pa['id']}/checks/{rtp['id']}/rtp").json()[0]["mos"] == 3.2
    kinds = sorted(e["kind"] for e in c.get(f"/api/clients/events?probe_id={pa['id']}").json())
    assert kinds == ["bandwidth_low", "mos_low"]

    ov = c.get("/api/clients/overview").json()
    s = next(x for x in ov["sites"] if x["site_id"] == site)
    cells = {
        cell["check_id"]: cell for cell in next(p for p in s["probes"] if p["probe_id"] == pa["id"])["cells"]
    }
    assert cells[wan["id"]]["state"] == "warn" and cells[wan["id"]]["text"] == "↓ 48 ↑ 19 Mbit/s"
    assert cells[rtp["id"]]["text"] == "MOS 3.20"

    # „Jetzt messen“ → genau einmal mit dem Heartbeat zugestellt; doppelter Klick → kein zweiter Auftrag
    a = c.post(f"/api/clients/probes/{pa['id']}/checks/{wan['id']}/run").json()
    b = c.post(f"/api/clients/probes/{pa['id']}/checks/{wan['id']}/run").json()
    assert a["id"] == b["id"] and a["type"] == "run_check"
    hb = c.post("/api/probe/v1/heartbeat", json={}, headers=pa["auth"]).json()
    assert hb["commands"] == [{"id": a["id"], "type": "run_check", "check_id": wan["id"]}]
    assert c.post("/api/probe/v1/heartbeat", json={}, headers=pa["auth"]).json()["commands"] == []


def test_settings_rtp_and_incidents_ignore_scheduled(env, monkeypatch):
    from app.services.rtp_reflector import rtp_reflector

    calls = []
    monkeypatch.setattr(rtp_reflector, "apply", lambda enabled, port: calls.append((enabled, port)))
    s = env["c"].put("/api/clients/settings", json={"rtp_enabled": True, "rtp_port": 5006}).json()
    assert (s["rtp_enabled"], s["rtp_port"]) == (True, 5006) and calls[-1] == (True, 5006)
    assert env["c"].put("/api/clients/settings", json={"rtp_port": 80}).status_code == 422
    env["c"].put("/api/clients/settings", json={"rtp_enabled": False, "rtp_port": 5004})
    assert env["c"].get("/api/clients/incidents?range=1h").status_code == 200
