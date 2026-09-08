"""Unit-Tests für Parser und Messberechnung."""

from __future__ import annotations

import json

from app.services.parsers import (
    compute_jitter,
    compute_rtt_stats,
    parse_fping_output,
    parse_ping_output,
    parse_ping_single,
    parse_speedtest_json,
)


def test_parse_speedtest_json_ok():
    raw = json.dumps(
        {
            "type": "result",
            "ping": {"latency": 12.5, "jitter": 1.2},
            "download": {"bandwidth": 12_500_000},  # Bytes/s -> 100 Mbps
            "upload": {"bandwidth": 1_250_000},  # -> 10 Mbps
            "packetLoss": 0.0,
            "server": {"id": 1234, "name": "Test", "location": "Berlin", "country": "DE"},
        }
    )
    parsed = parse_speedtest_json(raw)
    assert parsed.ok
    assert parsed.download_mbps == 100.0
    assert parsed.upload_mbps == 10.0
    assert parsed.ping_ms == 12.5
    assert parsed.jitter_ms == 1.2
    assert parsed.server_id == 1234
    assert "Berlin" in parsed.server_name


def test_parse_speedtest_json_error_log():
    raw = json.dumps({"type": "log", "message": "Configuration failed"})
    parsed = parse_speedtest_json(raw)
    assert not parsed.ok
    assert "Configuration failed" in parsed.error


def test_parse_speedtest_json_invalid():
    parsed = parse_speedtest_json("nicht json")
    assert not parsed.ok
    assert parsed.error


def test_compute_jitter():
    # |20-10| + |15-20| = 10 + 5 = 15; /2 = 7.5
    assert compute_jitter([10, 20, 15]) == 7.5
    assert compute_jitter([10]) is None
    assert compute_jitter([]) is None


def test_compute_rtt_stats_full():
    stats = compute_rtt_stats([10.0, 20.0, 30.0], sent=3)
    assert stats.loss_pct == 0.0
    assert stats.rtt_min == 10.0
    assert stats.rtt_avg == 20.0
    assert stats.rtt_max == 30.0
    assert stats.rtt_stddev is not None
    assert stats.jitter_ms == 10.0  # |20-10|=10, |30-20|=10, mean=10


def test_compute_rtt_stats_with_loss():
    stats = compute_rtt_stats([10.0, 12.0], sent=4)
    assert stats.loss_pct == 50.0
    assert stats.rtt_min == 10.0


def test_compute_rtt_stats_total_loss():
    stats = compute_rtt_stats([], sent=5)
    assert stats.loss_pct == 100.0
    assert stats.rtt_avg is None


def test_parse_fping_output():
    stderr = "1.1.1.1 : 12.3 8.9 - 10.1 9.7\n"
    stats = parse_fping_output(stderr, sent=5)
    # 4 Antworten von 5 -> 20% Verlust
    assert stats.loss_pct == 20.0
    assert stats.rtt_min == 8.9
    assert stats.rtt_max == 12.3


def test_parse_ping_output():
    stdout = (
        "PING 1.1.1.1 56 bytes\n"
        "64 bytes from 1.1.1.1: icmp_seq=1 ttl=59 time=12.3 ms\n"
        "64 bytes from 1.1.1.1: icmp_seq=2 ttl=59 time=11.8 ms\n"
    )
    stats = parse_ping_output(stdout, sent=2)
    assert stats.loss_pct == 0.0
    assert stats.rtt_min == 11.8
    assert stats.rtt_max == 12.3


def test_parse_ping_single():
    assert parse_ping_single("64 bytes from x: icmp_seq=1 ttl=59 time=9.4 ms") == 9.4
    assert parse_ping_single("Request timeout") is None


def test_wan_args_use_separator_and_reject_dash():
    """Härtung gegen Argument-Injection: '--' trennt Optionen von Operanden,
    ein mit '-' beginnender Host wird abgelehnt."""
    import pytest

    from app.services import wan_service

    fping = wan_service.build_fping_args("1.1.1.1", 5, 1000)
    assert "--" in fping and fping[-1] == "1.1.1.1"
    assert fping.index("--") < fping.index("1.1.1.1")

    ping = wan_service.build_ping_args("8.8.8.8", 5, 1000)
    assert "--" in ping and ping[-1] == "8.8.8.8"

    for bad in ("-oL/etc/passwd", "--flag"):
        with pytest.raises(ValueError):
            wan_service.build_fping_args(bad, 5, 1000)
        with pytest.raises(ValueError):
            wan_service.build_ping_args(bad, 5, 1000)


def test_ping_manager_rejects_dash_host():
    import pytest

    from app.services.ping_manager import _build_single_ping_args

    args = _build_single_ping_args("192.168.1.1")
    assert "--" in args and args[-1] == "192.168.1.1"
    with pytest.raises(ValueError):
        _build_single_ping_args("-eval")
