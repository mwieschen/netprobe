"""Unit-Tests für die tshark-Ausgabeparser."""

from __future__ import annotations

from app.services.tshark_parsers import (
    aggregate_dns_queries,
    aggregate_tls_sni,
    parse_conversations,
    parse_endpoints,
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
