"""Unit-Tests für die Traceroute-Parser (ohne echtes mtr/traceroute)."""

from __future__ import annotations

import json

from app.services import traceroute_service

_MTR_SAMPLE = json.dumps({
    "report": {
        "mtr": {"src": "10.0.0.5", "dst": "8.8.8.8", "tests": 3},
        "hubs": [
            {"count": 1, "host": "192.168.1.1", "Loss%": 0.0, "Snt": 3,
             "Last": 1.2, "Avg": 1.4, "Best": 1.1, "Wrst": 1.9, "StDev": 0.3},
            {"count": 2, "host": "???", "Loss%": 100.0, "Snt": 3,
             "Last": 0.0, "Avg": 0.0, "Best": 0.0, "Wrst": 0.0, "StDev": 0.0},
            {"count": 3, "host": "8.8.8.8", "Loss%": 0.0, "Snt": 3,
             "Last": 9.8, "Avg": 10.1, "Best": 9.5, "Wrst": 11.0, "StDev": 0.6},
        ],
    }
})

_TRACEROUTE_SAMPLE = """traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  192.168.1.1  0.512 ms  0.480 ms  0.470 ms
 2  * * *
 3  8.8.8.8  9.812 ms  9.700 ms  9.900 ms
"""


def test_parse_mtr_json():
    hops = traceroute_service.parse_mtr_json(_MTR_SAMPLE)
    assert len(hops) == 3
    assert hops[0]["ip"] == "192.168.1.1"
    assert hops[0]["loss_pct"] == 0.0
    assert hops[0]["latency_avg_ms"] == 1.4
    assert hops[1]["ip"] is None
    assert hops[1]["loss_pct"] == 100.0
    assert hops[2]["ip"] == "8.8.8.8"


def test_parse_mtr_json_garbage():
    assert traceroute_service.parse_mtr_json("not json") == []
    assert traceroute_service.parse_mtr_json('{"report": {}}') == []


def test_parse_traceroute_text():
    hops = traceroute_service.parse_traceroute_text(_TRACEROUTE_SAMPLE)
    assert len(hops) == 3
    assert hops[0]["ip"] == "192.168.1.1"
    assert hops[0]["latency_avg_ms"] is not None
    assert hops[1]["ip"] is None
    assert hops[1]["loss_pct"] == 100.0
    assert hops[2]["ip"] == "8.8.8.8"


def test_compare_aligns_by_hop(monkeypatch):
    class FakeHop:
        def __init__(self, n, ip, lat):
            self.hop_number = n
            self.ip = ip
            self.hostname = None
            self.latency_avg_ms = lat

    class FakeTr:
        def __init__(self, tid, hops):
            self.id = tid
            self.target = "8.8.8.8"
            self.hops = hops

        class _TS:
            @staticmethod
            def isoformat():
                return "2026-09-09T00:00:00+00:00"

        ts = _TS()

    a = FakeTr(1, [FakeHop(1, "192.168.1.1", 1.0), FakeHop(2, "10.0.0.1", 5.0)])
    b = FakeTr(2, [FakeHop(1, "192.168.1.1", 1.2), FakeHop(2, "10.9.9.9", 8.0)])
    out = traceroute_service.compare(a, b)
    assert out["path_changed"] is True
    assert out["rows"][0]["changed"] is False
    assert out["rows"][1]["changed"] is True
    assert out["rows"][1]["delta_ms"] == 3.0
