"""Unit-Tests für die tshark-Ausgabeparser."""

from __future__ import annotations

from app.services.tshark_parsers import (
    aggregate_dns_queries,
    aggregate_tls_sni,
    compute_io_buckets,
    parse_capture_metrics,
    parse_conversations,
    parse_endpoints,
    parse_epoch_len_lines,
    parse_expert_info,
    parse_protocol_hierarchy,
)


def test_parse_protocol_hierarchy():
    text = (
        "eth                                      frames:100 bytes:12000\n"
        "  ip                                     frames:90 bytes:11000\n"
        "    tcp                                  frames:80 bytes:10000\n"
    )
    entries = parse_protocol_hierarchy(text)
    assert entries[0]["protocol"] == "eth"
    assert entries[0]["frames"] == 100
    assert entries[0]["level"] == 0
    assert entries[1]["level"] == 1
    assert entries[2]["protocol"] == "tcp"


def test_parse_endpoints_sorted():
    text = (
        "1.1.1.1   10  1000  5  500  5  500\n"
        "8.8.8.8   20  9000  10  4000  10  5000\n"
    )
    eps = parse_endpoints(text)
    assert eps[0]["address"] == "8.8.8.8"  # nach bytes sortiert
    assert eps[0]["bytes"] == 9000


def test_parse_conversations():
    text = "10.0.0.1:1234 <-> 1.1.1.1:443 5 500 6 800 11 1300\n"
    conv = parse_conversations(text)
    assert conv[0]["endpoint_a"] == "10.0.0.1:1234"
    assert conv[0]["bytes_total"] == 1300


def test_aggregate_dns_queries():
    lines = ["example.com", "example.com", "test.de"]
    result = aggregate_dns_queries(lines)
    assert result[0] == {"name": "example.com", "count": 2}


def test_aggregate_tls_sni():
    lines = ["api.example.com", "api.example.com", "cdn.example.com"]
    result = aggregate_tls_sni(lines)
    assert result[0]["server_name"] == "api.example.com"
    assert result[0]["count"] == 2


def test_parse_expert_info():
    text = (
        "Warnings (5)\n"
        "     Sequence  TCP   Previous segment not captured 3\n"
        "Notes (2)\n"
        "     Sequence  TCP   Duplicate ACK 2\n"
    )
    info = parse_expert_info(text)
    assert any(i["severity"] == "Warnings" and i["count"] == 3 for i in info)
    assert any(i["severity"] == "Notes" for i in info)


def test_parse_epoch_len_lines():
    text = "1700000000.000000\t100\n1700000000.500000\t200\n\nkaputt\n"
    rows = parse_epoch_len_lines(text)
    assert rows == [(1700000000.0, 100), (1700000000.5, 200)]


def test_compute_io_buckets_empty():
    result = compute_io_buckets([], buckets=10)
    assert result["total_packets"] == 0
    assert result["series"] == []


def test_parse_capture_metrics():
    # Felder: eth.dst.ig  eth.dst  frame.len  proto  ip.src  ip.dst  tcp.flags
    rows = "\n".join(
        [
            "0\taa:bb:cc:00:00:01\t100\tTCP\t10.0.0.1\t10.0.0.2\t0x0018",
            "0\taa:bb:cc:00:00:02\t200\tTCP\t10.0.0.2\t10.0.0.1\t0x0010",
            "1\t01:00:5e:00:00:fb\t80\tMDNS\t10.0.0.3\t224.0.0.251\t",
            "1\tff:ff:ff:ff:ff:ff\t60\tARP\t\t\t",
            "0\t\t50\tTCP\t10.0.0.1\t10.0.0.9\t0x0002",
        ]
    )
    m = parse_capture_metrics(rows, top_n=5)
    d = m["frame_distribution"]
    assert d["total"] == 5
    assert d["unicast"] == 2 and d["broadcast"] == 1 and d["multicast"] == 1 and d["other"] == 1
    assert d["broadcast_pct"] == 20.0
    assert m["tcp_packets"] == 3
    # Top-Protokoll TCP: 3 Pakete, 350 Bytes
    tcp = next(p for p in m["top_protocols"] if p["protocol"] == "TCP")
    assert tcp["packets"] == 3 and tcp["bytes"] == 350
    # Conversation 10.0.0.1<->10.0.0.2: 2 Pakete, 300 Bytes (richtungsunabhängig)
    conv = next(
        c for c in m["top_conversations"]
        if {c["addr_a"], c["addr_b"]} == {"10.0.0.1", "10.0.0.2"}
    )
    assert conv["packets"] == 2 and conv["bytes"] == 300


def test_compute_io_buckets_basic():
    # 4 Pakete über 2 Sekunden, 2 Buckets à 1 s
    rows = [(0.0, 100), (0.4, 100), (1.2, 400), (2.0, 400)]
    result = compute_io_buckets(rows, buckets=2)
    assert result["total_packets"] == 4
    assert result["total_bytes"] == 1000
    assert result["duration_s"] == 2.0
    assert len(result["series"]) == 2
    # erstes Intervall: 2 Pakete / 1 s
    assert result["series"][0]["packets_per_s"] == 2.0
    # bits/s im ersten Intervall: 200 Bytes * 8 / 1 s
    assert result["series"][0]["bits_per_s"] == 1600.0
