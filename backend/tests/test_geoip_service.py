"""Tests für die Geo-/ASN-Auflösung (ip-api.com wird gemockt, keine echten Requests)."""

from __future__ import annotations

import pytest

from app.services import geoip_service


def test_is_public_filters_private_and_bogon():
    assert geoip_service._is_public("8.8.8.8") is True
    assert geoip_service._is_public("1.1.1.1") is True
    assert geoip_service._is_public("192.168.1.1") is False
    assert geoip_service._is_public("10.0.0.1") is False
    assert geoip_service._is_public("172.16.5.4") is False
    assert geoip_service._is_public("127.0.0.1") is False
    assert geoip_service._is_public("169.254.1.1") is False
    assert geoip_service._is_public("100.64.0.1") is False  # CGNAT
    assert geoip_service._is_public("224.0.0.1") is False  # Multicast
    assert geoip_service._is_public("kein-ip") is False


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_lookup_uses_online_and_caches(monkeypatch):
    from app.db.session import session_scope
    from app.models.traceroute import GeoipCache

    calls: list = []

    def fake_post(url, json, timeout):  # noqa: A002 - Signatur von httpx.post
        calls.append(json)
        return _FakeResp([
            {"query": "8.8.8.8", "status": "success", "country": "United States",
             "city": "Mountain View", "lat": 37.4, "lon": -122.1,
             "as": "AS15169 Google LLC", "isp": "Google LLC"},
        ])

    monkeypatch.setattr(geoip_service.httpx, "post", fake_post)
    # lokale DB deaktivieren, damit der Online-Pfad greift
    monkeypatch.setattr(geoip_service, "_mmdb_lookup", lambda ip: None)
    geoip_service._rate_calls.clear()

    with session_scope() as db:
        db.query(GeoipCache).delete()
        db.commit()

        res = geoip_service.lookup(db, ["8.8.8.8", "192.168.1.1"])
        assert res["8.8.8.8"]["country"] == "United States"
        assert res["8.8.8.8"]["asn"] == "AS15169"
        assert "192.168.1.1" not in res  # privat -> nie angefragt

        # zweiter Aufruf: aus dem Cache, kein weiterer HTTP-Call
        res2 = geoip_service.lookup(db, ["8.8.8.8"])
        assert res2["8.8.8.8"]["city"] == "Mountain View"

    assert len(calls) == 1


def test_lookup_disabled_returns_empty(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "geoip_enabled", False)
    from app.db.session import session_scope

    with session_scope() as db:
        assert geoip_service.lookup(db, ["8.8.8.8"]) == {}


@pytest.fixture(autouse=True)
def _reset_rate():
    geoip_service._rate_calls.clear()
    yield
    geoip_service._rate_calls.clear()
