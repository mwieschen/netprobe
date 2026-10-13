"""Tests für Network Diagnostics: reine Klassifikationslogik sowie die
Orchestrierung mit gemockten Sub-Test-Services (jeder Sub-Test hat seine eigene
Testsuite; hier wird nur geprüft, dass diagnostics_service sie korrekt aufruft,
zusammenführt und klassifiziert)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import diagnostics_service as ds
from app.services.parsers import RttStats

# --------------------------------------------------------------------------- #
# classify_target / classify_run (reine Funktionen)
# --------------------------------------------------------------------------- #

def test_classify_target_ok():
    ping = {"error": None, "loss_pct": 0.0, "jitter_ms": 1.0, "rtt_avg": 10.0}
    status, issues = ds.classify_target(ping, None, None)
    assert status == "OK"
    assert issues == []


def test_classify_target_packet_loss_warns():
    ping = {"error": None, "loss_pct": 50.0, "jitter_ms": 1.0, "rtt_avg": 10.0}
    status, issues = ds.classify_target(ping, None, None)
    assert status == "WARN"
    assert "packet_loss_high" in issues


def test_classify_target_ping_failed():
    ping = {"error": "Timeout", "loss_pct": None, "jitter_ms": None, "rtt_avg": None}
    status, issues = ds.classify_target(ping, None, None)
    assert status == "WARN"
    assert "ping_failed" in issues


def test_classify_target_retrans_critical_is_always_kritisch():
    retrans = {"rate_percent": 99.0}
    status, issues = ds.classify_target(None, retrans, None)
    assert status == "KRITISCH"
    assert "retrans_critical" in issues


def test_classify_target_mtu_low():
    mtu = {"ok": True, "path_mtu": 1400}
    status, issues = ds.classify_target(None, None, mtu)
    assert status == "WARN"
    assert "mtu_low" in issues


def test_classify_target_many_warn_issues_escalate_to_kritisch():
    ping = {"error": None, "loss_pct": 50.0, "jitter_ms": 100.0, "rtt_avg": 500.0}
    mtu = {"ok": True, "path_mtu": 1000}
    status, issues = ds.classify_target(ping, None, mtu)
    assert len(issues) >= 3
    assert status == "KRITISCH"


def test_classify_run_worst_of_targets():
    assert ds.classify_run(["OK", "OK"], False) == "OK"
    assert ds.classify_run(["OK", "WARN"], False) == "WARN"
    assert ds.classify_run(["OK", "KRITISCH"], False) == "KRITISCH"
    assert ds.classify_run(["OK"], True) == "WARN"


# --------------------------------------------------------------------------- #
# run_diagnostics (Orchestrierung, alle Sub-Tests gemockt)
# --------------------------------------------------------------------------- #

@pytest.fixture
def mocked_subtests(monkeypatch):
    monkeypatch.setattr(ds.wan_service, "measure", lambda host, count, timeout_ms: (
        RttStats(loss_pct=0.0, rtt_min=9.0, rtt_avg=10.0, rtt_max=11.0, jitter_ms=1.0, rtts=[10.0]), None
    ))
    monkeypatch.setattr(ds.mtu_service, "discover_path_mtu", lambda target, lo, hi: {
        "target": target, "ok": True, "path_mtu": 1500, "min_mtu": lo, "max_mtu": hi,
        "probe_count": 3, "probes": [], "error": None,
    })

    fake_hop = SimpleNamespace(hop_number=1, hostname=None, ip="10.0.0.1")
    fake_tr = SimpleNamespace(
        id=1, completed=True, dest_reached=True, total_hops=5, avg_latency_ms=12.0,
        error=None, hops=[fake_hop],
    )
    monkeypatch.setattr(ds.traceroute_service, "run", lambda db, **kw: fake_tr)

    fake_speedtest = SimpleNamespace(
        ok=True, download_mbps=500.0, upload_mbps=50.0, ping_ms=5.0, jitter_ms=1.0, error=None,
    )
    monkeypatch.setattr(ds.speedtest_service, "run_speedtest", lambda db, **kw: fake_speedtest)

    monkeypatch.setattr(ds.capture_service, "validate_iface", lambda iface: iface)
    monkeypatch.setattr(ds.capture_service, "run_capture_window", lambda *a, **k: None)

    return fake_tr


def test_run_diagnostics_ping_mtu_traceroute_speedtest(mocked_subtests):
    run = ds.run_diagnostics(
        targets=[{"name": "Cloudflare", "host": "1.1.1.1"}],
        tests={"ping": True, "mtu": True, "traceroute": True, "speedtest": True,
               "retrans": False, "broadcast": False},
        interface="eth0", duration_s=10,
    )
    assert run.status == "done"
    assert run.overall_status == "OK"

    import json
    results = json.loads(run.results_json)
    assert results["speedtest"]["download_mbps"] == 500.0
    target = results["targets"][0]
    assert target["host"] == "1.1.1.1"
    assert target["ping"]["loss_pct"] == 0.0
    assert target["mtu"]["path_mtu"] == 1500
    assert target["traceroute"]["total_hops"] == 5
    assert target["status"] == "OK"


def test_run_diagnostics_requires_targets_when_per_target_tests_selected(mocked_subtests):
    with pytest.raises(ds.DiagnosticsError, match="Ziel"):
        ds.run_diagnostics(targets=[], tests={"ping": True}, interface="eth0", duration_s=10)


def test_run_diagnostics_requires_at_least_one_test(mocked_subtests):
    with pytest.raises(ds.DiagnosticsError, match="Test"):
        ds.run_diagnostics(
            targets=[{"name": "x", "host": "1.1.1.1"}],
            tests=dict.fromkeys(ds.TEST_TYPES, False),
            interface="eth0", duration_s=10,
        )


def test_run_diagnostics_broadcast_only_needs_no_targets(mocked_subtests, monkeypatch):
    monkeypatch.setattr(ds.capture_service, "run_capture_window", lambda *a, **k: None)
    run = ds.run_diagnostics(
        targets=[], tests={"broadcast": True}, interface="eth0", duration_s=10,
    )
    assert run.status == "done"
    import json
    results = json.loads(run.results_json)
    assert results["targets"] == []
    assert results["broadcast"]["error"] is not None  # Mitschnitt gemockt als fehlgeschlagen


def test_run_diagnostics_retrans_combines_all_targets_in_one_call(mocked_subtests, monkeypatch):
    calls: list[list[dict]] = []

    fake_target_a = SimpleNamespace(target_ip="1.1.1.1", rate_percent=0.0, retransmissions=0,
                                     packets=10, connection_errors=0)
    fake_target_b = SimpleNamespace(target_ip="8.8.8.8", rate_percent=5.0, retransmissions=5,
                                     packets=10, connection_errors=1)
    fake_test = SimpleNamespace(
        id=42, status="done", classification="ISP_OR_WAN_LINK_ISSUE",
        retransmission_rate_percent=2.5, error=None, targets=[fake_target_a, fake_target_b],
    )

    def _fake_run_test(db, **kwargs):
        calls.append(kwargs["targets"])
        return fake_test

    monkeypatch.setattr(ds.retransmission_service, "run_test", _fake_run_test)
    monkeypatch.setattr(
        ds.retransmission_service, "resolve_active_target",
        lambda host, port, protocol="tcp": (host, port),
    )

    run = ds.run_diagnostics(
        targets=[{"name": "A", "host": "1.1.1.1"}, {"name": "B", "host": "8.8.8.8"}],
        tests={"retrans": True, "ping": False, "mtu": False, "traceroute": False,
               "speedtest": False, "broadcast": False},
        interface="eth0", duration_s=10,
    )
    assert len(calls) == 1  # EIN gemeinsamer Aufruf für beide Ziele, nicht zwei
    assert len(calls[0]) == 2

    import json
    results = json.loads(run.results_json)
    by_host = {t["host"]: t for t in results["targets"]}
    assert by_host["1.1.1.1"]["retrans"]["rate_percent"] == 0.0
    assert by_host["8.8.8.8"]["retrans"]["rate_percent"] == 5.0
    assert by_host["8.8.8.8"]["status"] in ("WARN", "KRITISCH")


def test_run_diagnostics_surfaces_retrans_tool_failure_instead_of_silently_dropping_it(
    mocked_subtests, monkeypatch,
):
    """Regressionstest: ein komplett fehlgeschlagener Retransmission-Sub-Test
    (z. B. fehlendes dumpcap) erzeugt ein RetransmissionTest mit status="error"
    und OHNE RetransmissionTarget-Zeilen. Das darf nicht dazu führen, dass der
    Nutzer überhaupt nicht erfährt, dass der von ihm ausgewählte Test gelaufen
    (und gescheitert) ist."""
    fake_test = SimpleNamespace(
        id=99, status="error", classification=None, retransmission_rate_percent=None,
        error="Benötigtes Programm 'dumpcap' wurde nicht gefunden.", targets=[],
    )
    monkeypatch.setattr(ds.retransmission_service, "run_test", lambda db, **kw: fake_test)
    monkeypatch.setattr(
        ds.retransmission_service, "resolve_active_target",
        lambda host, port, protocol="tcp": (host, port),
    )

    run = ds.run_diagnostics(
        targets=[{"name": "Cloudflare", "host": "1.1.1.1"}],
        tests={"retrans": True, "ping": False, "mtu": False, "traceroute": False,
               "speedtest": False, "broadcast": False},
        interface="eth0", duration_s=10,
    )

    import json
    results = json.loads(run.results_json)
    assert results["retrans_error"] is not None
    assert "dumpcap" in results["retrans_error"]
    assert results["targets"][0]["retrans"] is None
    # Ein fehlgeschlagener, aber explizit angeforderter Test eskaliert den Gesamtstatus.
    assert run.overall_status != "OK"
