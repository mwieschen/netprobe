"""Tests für TCP-Retransmission-Monitoring: reine Parsing-/Klassifikationslogik
sowie ein End-to-End-Lauf mit gemockter Aufnahme/tshark-Analyse."""

from __future__ import annotations

import json

from app.services import retransmission_service as rs

# --------------------------------------------------------------------------- #
# parse_retransmission_lines
# --------------------------------------------------------------------------- #

def test_parse_retransmission_lines_basic():
    text = (
        "1700000000.0\t10.0.0.5\t8.8.8.8\t51000\t443\t\t1400\n"
        "1700000000.5\t10.0.0.5\t8.8.8.8\t51000\t443\t1\t1400\n"
        "\n"
        "garbage-line-without-enough-fields\n"
    )
    rows = rs.parse_retransmission_lines(text)
    assert len(rows) == 2
    assert rows[0]["retrans"] is False
    assert rows[1]["retrans"] is True
    assert rows[1]["dport"] == 443
    assert rows[1]["length"] == 1400


# --------------------------------------------------------------------------- #
# aggregate_retransmissions
# --------------------------------------------------------------------------- #

def _row(t, src, dst, sport, dport, retrans, length=1400):
    return {
        "t": t, "src": src, "dst": dst, "sport": sport, "dport": dport,
        "retrans": retrans, "length": length,
    }


def test_aggregate_retransmissions_direction_and_grouping():
    local_ip = "10.0.0.5"
    rows = [
        _row(1.0, local_ip, "8.8.8.8", 51000, 443, False),
        _row(2.0, local_ip, "8.8.8.8", 51000, 443, True),  # upload retrans
        _row(3.0, "1.1.1.1", local_ip, 443, 52000, True),  # download retrans
        _row(4.0, local_ip, "1.1.1.1", 52000, 443, False),
    ]
    agg = rs.aggregate_retransmissions(rows, local_ip)
    assert agg["total_packets"] == 4
    assert agg["total_retransmissions"] == 2
    targets = {t["ip"]: t for t in agg["targets"]}
    assert targets["8.8.8.8"]["direction"] == "upload"
    assert targets["1.1.1.1"]["direction"] == "download"
    assert targets["8.8.8.8"]["is_private"] is False


def test_aggregate_retransmissions_unknown_direction_without_local_ip():
    rows = [_row(1.0, "192.168.1.5", "192.168.1.1", 1000, 80, True)]
    agg = rs.aggregate_retransmissions(rows, local_ip=None)
    assert agg["targets"][0]["direction"] == "unknown"
    assert agg["targets"][0]["is_private"] is True


def test_aggregate_retransmissions_configured_includes_zero_traffic_target():
    local_ip = "10.0.0.5"
    rows = [
        _row(1.0, local_ip, "8.8.8.8", 51000, 443, False),
        _row(2.0, local_ip, "8.8.8.8", 51000, 443, True),
    ]
    # 1.1.1.1 war konfiguriert, aber kein einziges Paket wurde dafür erfasst
    # (z. B. Verbindung schlug immer fehl, bevor überhaupt ein SYN rausging).
    agg = rs.aggregate_retransmissions(rows, local_ip, configured=[("8.8.8.8", 443), ("1.1.1.1", 443)])
    targets = {t["ip"]: t for t in agg["targets"]}
    assert targets["8.8.8.8"]["packets"] == 2
    assert targets["8.8.8.8"]["retransmissions"] == 1
    assert targets["1.1.1.1"]["packets"] == 0
    assert targets["1.1.1.1"]["retransmissions"] == 0
    assert targets["1.1.1.1"]["port"] == 443
    # Kein doppelter Eintrag für dasselbe Ziel (Port-Mismatch-Bug beim Platzhalter).
    assert len(agg["targets"]) == 2


def test_aggregate_retransmissions_configured_filters_unrelated_traffic():
    rows = [
        _row(1.0, "10.0.0.5", "8.8.8.8", 51000, 443, True),
        _row(2.0, "10.0.0.5", "9.9.9.9", 51000, 443, True),  # nicht konfiguriert
    ]
    agg = rs.aggregate_retransmissions(rows, "10.0.0.5", configured=[("8.8.8.8", 443)])
    assert len(agg["targets"]) == 1
    assert agg["targets"][0]["ip"] == "8.8.8.8"
    # total_retransmissions/total_packets bleiben unverändert (gesamte Aufnahme).
    assert agg["total_packets"] == 2
    assert agg["total_retransmissions"] == 2


# --------------------------------------------------------------------------- #
# compute_timeline
# --------------------------------------------------------------------------- #

def test_compute_timeline_buckets():
    rows = [_row(float(i), "10.0.0.5", "8.8.8.8", 1, 2, i % 5 == 0) for i in range(10)]
    timeline = rs.compute_timeline(rows, buckets=5)
    assert len(timeline) == 5
    assert sum(p["rate_percent"] >= 0 for p in timeline) == 5


def test_compute_timeline_empty():
    assert rs.compute_timeline([]) == []


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #

def _target(ip, is_private, retrans, up=0, down=0):
    return {
        "ip": ip, "port": 443, "is_private": is_private, "retransmissions": retrans,
        "direction_counts": {"upload": up, "download": down, "unknown": 0},
    }


def test_classify_ok_when_no_retransmissions():
    cls, _ = rs.classify(0, [])
    assert cls == "OK"


def test_classify_local_network_issue():
    targets = [_target("192.168.1.1", True, 10, up=5, down=5)]
    cls, msg = rs.classify(10, targets)
    assert cls == "LOCAL_NETWORK_ISSUE"
    assert "lokale" in msg.lower() or "lokalen" in msg.lower()


def test_classify_remote_server_issue_single_public_target():
    targets = [_target("8.8.8.8", False, 10, up=5, down=5)]
    cls, _ = rs.classify(10, targets)
    assert cls == "REMOTE_SERVER_ISSUE"


def test_classify_uplink_congestion():
    targets = [_target("8.8.8.8", False, 10, up=10, down=0), _target("1.1.1.1", False, 5, up=5, down=0)]
    cls, _ = rs.classify(15, targets)
    assert cls == "UPLINK_CONGESTION"


def test_classify_downlink_congestion():
    targets = [_target("8.8.8.8", False, 10, up=0, down=10), _target("1.1.1.1", False, 5, up=0, down=5)]
    cls, _ = rs.classify(15, targets)
    assert cls == "DOWNLINK_CONGESTION"


def test_classify_isp_or_wan_link_issue_mixed_direction():
    targets = [_target("8.8.8.8", False, 10, up=10, down=0), _target("1.1.1.1", False, 5, up=0, down=5)]
    cls, _ = rs.classify(15, targets)
    assert cls == "ISP_OR_WAN_LINK_ISSUE"


def test_classify_unknown_mixed_lan_and_wan():
    targets = [_target("192.168.1.1", True, 10), _target("8.8.8.8", False, 5)]
    cls, _ = rs.classify(15, targets)
    assert cls == "UNKNOWN"


# --------------------------------------------------------------------------- #
# Aktive Tests: Zielauflösung, Connect-Proben
# --------------------------------------------------------------------------- #

def test_resolve_active_target_rejects_non_tcp():
    import pytest

    with pytest.raises(ValueError, match="TCP"):
        rs.resolve_active_target("1.1.1.1", 443, protocol="udp")


def test_resolve_active_target_rejects_bad_port():
    import pytest

    with pytest.raises(ValueError, match="Port"):
        rs.resolve_active_target("1.1.1.1", 0)


def test_resolve_active_target_resolves_hostname(monkeypatch):
    monkeypatch.setattr(rs.socket, "gethostbyname", lambda host: "203.0.113.5")
    ip, port = rs.resolve_active_target("example.test", 443)
    assert ip == "203.0.113.5"
    assert port == 443


def test_resolve_active_target_rejects_unresolvable_host(monkeypatch):
    import pytest

    def _raise(host):
        raise OSError("nxdomain")

    monkeypatch.setattr(rs.socket, "gethostbyname", _raise)
    with pytest.raises(ValueError, match="nicht auflösbar"):
        rs.resolve_active_target("does-not-exist.test", 443)


def test_tcp_connect_probe_success(monkeypatch):
    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rs.socket, "create_connection", lambda *a, **k: _FakeConn())
    ok, latency_ms = rs._tcp_connect_probe("1.1.1.1", 443)
    assert ok is True
    assert latency_ms is not None and latency_ms >= 0


def test_tcp_connect_probe_failure(monkeypatch):
    def _raise(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(rs.socket, "create_connection", _raise)
    ok, latency_ms = rs._tcp_connect_probe("1.1.1.1", 443)
    assert ok is False
    assert latency_ms is None


def test_run_active_traffic_concurrent_runs_all_targets(monkeypatch):
    calls: list[tuple[str, int, float]] = []

    def _fake_probe_loop(ip, port, duration_s, interval_s=0.5):
        calls.append((ip, port, duration_s))
        return rs._ConnStats(attempts=1, errors=0, latencies_ms=[5.0])

    monkeypatch.setattr(rs, "_probe_loop", _fake_probe_loop)
    stats = rs._run_active_traffic([("1.1.1.1", 443), ("8.8.8.8", 443)], 10.0, concurrent=True)
    assert len(stats) == 2
    assert all(d == 10.0 for _ip, _port, d in calls)


def test_run_active_traffic_sequential_splits_duration(monkeypatch):
    calls: list[float] = []

    def _fake_probe_loop(ip, port, duration_s, interval_s=0.5):
        calls.append(duration_s)
        return rs._ConnStats()

    monkeypatch.setattr(rs, "_probe_loop", _fake_probe_loop)
    rs._run_active_traffic([("1.1.1.1", 443), ("8.8.8.8", 443)], 10.0, concurrent=False)
    assert calls == [5.0, 5.0]


# --------------------------------------------------------------------------- #
# run_test (End-to-End mit gemockter Aufnahme/tshark-Analyse)
# --------------------------------------------------------------------------- #

def test_run_test_persists_targets_and_timeline(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.db.session import session_scope
    from app.services import audit
    from app.services.proc import ProcResult

    fake_pcap = tmp_path / "fake.pcapng"
    fake_pcap.write_bytes(b"\x0a\x0d\x0d\x0a")

    monkeypatch.setattr(rs.capture_service, "run_capture_window", lambda *a, **k: fake_pcap)
    monkeypatch.setattr(rs, "_local_ipv4", lambda iface: "10.0.0.5")

    tshark_output = (
        "1.0\t10.0.0.5\t8.8.8.8\t51000\t443\t\t1400\n"
        "2.0\t10.0.0.5\t8.8.8.8\t51000\t443\t1\t1400\n"
        "3.0\t10.0.0.5\t8.8.8.8\t51000\t443\t1\t1400\n"
    )
    monkeypatch.setattr(
        rs, "run_sync", lambda *a, **k: ProcResult(returncode=0, stdout=tshark_output, stderr="")
    )

    recorded: list[str] = []
    monkeypatch.setattr(audit, "record", lambda db, action, **kw: recorded.append(action))

    orig_warn = settings.retrans_warn_threshold_pct
    settings.retrans_warn_threshold_pct = 1.0
    try:
        with session_scope() as db:
            test = rs.run_test(db, interface="eth0", duration_s=10, source="manual")
            test_id = test.id
            assert test.status == "done"
            assert test.total_packets == 3
            assert test.total_retransmissions == 2
            assert test.classification == "REMOTE_SERVER_ISSUE"
            assert len(test.targets) == 1
            assert test.targets[0].direction == "upload"
            assert len(test.timeline) > 0
        assert "retransmission_warn" in recorded or "retransmission_alert" in recorded

        # PCAP wird nach der Analyse gelöscht.
        assert not fake_pcap.exists()

        with session_scope() as db:
            from app.models.retransmission import RetransmissionTest

            reloaded = db.get(RetransmissionTest, test_id)
            assert reloaded is not None
            assert reloaded.total_retransmissions == 2
    finally:
        settings.retrans_warn_threshold_pct = orig_warn


def test_run_test_active_mode_persists_mode_and_connection_stats(monkeypatch, tmp_path):
    from app.db.session import session_scope
    from app.services.proc import ProcResult

    fake_pcap = tmp_path / "active.pcapng"
    fake_pcap.write_bytes(b"\x0a\x0d\x0d\x0a")

    monkeypatch.setattr(rs.capture_service, "run_capture_window", lambda *a, **k: fake_pcap)
    monkeypatch.setattr(rs, "_local_ipv4", lambda iface: "10.0.0.5")
    monkeypatch.setattr(rs, "resolve_active_target", lambda host, port, protocol="tcp": (host, port))
    monkeypatch.setattr(rs, "ensure_tool", lambda binary: binary)

    def _fake_probe_loop(ip, port, duration_s, interval_s=0.5):
        return rs._ConnStats(attempts=4, errors=1, latencies_ms=[10.0, 20.0, 30.0])

    monkeypatch.setattr(rs, "_probe_loop", _fake_probe_loop)

    tshark_output = (
        "1.0\t10.0.0.5\t1.1.1.1\t51000\t443\t\t1400\n"
        "2.0\t10.0.0.5\t1.1.1.1\t51000\t443\t1\t1400\n"
    )
    monkeypatch.setattr(
        rs, "run_sync", lambda *a, **k: ProcResult(returncode=0, stdout=tshark_output, stderr="")
    )

    with session_scope() as db:
        test = rs.run_test(
            db, interface="eth0", duration_s=10, source="manual", mode="active",
            targets=[{"host": "1.1.1.1", "port": 443, "protocol": "tcp"}], concurrent=True,
        )
        assert test.status == "done"
        assert test.test_mode == "active"
        assert json.loads(test.target_ips) == [{"host": "1.1.1.1", "ip": "1.1.1.1", "port": 443}]
        assert len(test.targets) == 1
        t = test.targets[0]
        assert t.connection_attempts == 4
        assert t.connection_errors == 1
        assert t.avg_connect_latency_ms == 20.0


def test_run_test_active_mode_requires_targets():
    import pytest

    from app.db.session import session_scope

    with session_scope() as db, pytest.raises(ValueError, match="Ziel"):
        rs.run_test(db, interface="eth0", duration_s=10, mode="active", targets=None)


def test_run_test_active_mode_fails_fast_without_waiting_full_duration(monkeypatch):
    """Fehlt dumpcap, darf der aktive Test nicht erst die volle Dauer mit
    Connect-Proben verbringen, bevor der Fehler sichtbar wird (Regressionstest
    für einen Bug, der beim manuellen Testen auffiel)."""
    import time as time_mod

    from app.db.session import session_scope
    from app.services.proc import ToolNotFoundError

    def _raise(_binary):
        raise ToolNotFoundError("dumpcap")

    monkeypatch.setattr(rs, "ensure_tool", _raise)

    def _fail_if_called(*a, **k):
        raise AssertionError("_run_active_traffic sollte nach fehlgeschlagenem ensure_tool nicht laufen")

    monkeypatch.setattr(rs, "_run_active_traffic", _fail_if_called)
    monkeypatch.setattr(rs, "resolve_active_target", lambda host, port, protocol="tcp": (host, port))

    with session_scope() as db:
        start = time_mod.monotonic()
        test = rs.run_test(
            db, interface="eth0", duration_s=60, mode="active",
            targets=[{"host": "1.1.1.1", "port": 443, "protocol": "tcp"}],
        )
        elapsed = time_mod.monotonic() - start
    assert test.status == "error"
    assert "dumpcap" in test.error
    assert elapsed < 5


def test_run_test_active_mode_rejects_too_many_targets():
    import pytest

    from app.db.session import session_scope

    many = [{"host": "1.1.1.1", "port": 443, "protocol": "tcp"}] * (rs.MAX_ACTIVE_TARGETS + 1)
    with session_scope() as db, pytest.raises(ValueError, match="Höchstens"):
        rs.run_test(db, interface="eth0", duration_s=10, mode="active", targets=many)


# --------------------------------------------------------------------------- #
# Standard-Interface-Auflösung (früher fest "eth0")
# --------------------------------------------------------------------------- #

def _fake_sys_net(tmp_path, monkeypatch, states):
    from app.services import capture_service as cs

    for name, state in states.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "operstate").write_text(state + "\n")
    monkeypatch.setattr(cs, "_SYS_NET", tmp_path)
    monkeypatch.setattr(cs, "default_route_interface", lambda: "ens18")
    return cs


def test_resolve_default_interface_auto_uses_default_route(tmp_path, monkeypatch):
    cs = _fake_sys_net(tmp_path, monkeypatch, {"ens18": "up"})
    assert cs.resolve_default_interface("auto") == "ens18"
    assert cs.resolve_default_interface("") == "ens18"


def test_resolve_default_interface_missing_or_down_falls_back(tmp_path, monkeypatch):
    cs = _fake_sys_net(tmp_path, monkeypatch, {"ens18": "up", "eth0": "down"})
    assert cs.resolve_default_interface("eth0") == "ens18"   # down
    assert cs.resolve_default_interface("eth9") == "ens18"   # existiert nicht


def test_resolve_default_interface_keeps_explicit_active_iface(tmp_path, monkeypatch):
    cs = _fake_sys_net(tmp_path, monkeypatch, {"ens18": "up", "eth1": "up", "wg0": "unknown"})
    assert cs.resolve_default_interface("eth1") == "eth1"
    assert cs.resolve_default_interface("wg0") == "wg0"


def test_default_route_interface_parses_ip_route(monkeypatch):
    from types import SimpleNamespace

    from app.services import capture_service as cs

    out = "default via 192.168.1.1 dev enp3s0 proto dhcp src 192.168.1.20 metric 100\n"
    monkeypatch.setattr(cs, "run_sync", lambda args, timeout: SimpleNamespace(stdout=out))
    assert cs.default_route_interface() == "enp3s0"
