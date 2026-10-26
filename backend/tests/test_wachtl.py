"""Wachtl-Clients: Ingest-Parser, SPKI-Pin, Zustand und Probe-API v1 im Zusammenspiel."""

from __future__ import annotations

import gzip
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.wachtl_ingest import (
    BodyTooLarge,
    classify_check,
    decompress_gzip,
    parse_ingest,
    spki_pin_from_pem,
)

# Selbstsigniertes EC-Testzertifikat; Pin per
# openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64
TEST_CERT = """-----BEGIN CERTIFICATE-----
MIIBhTCCASugAwIBAgIUT4foxQW80AMik5ZZJ/aLPoMAsoQwCgYIKoZIzj0EAwIw
GDEWMBQGA1UEAwwNbmV0cHJvYmUtdGVzdDAeFw0yNjEwMjMxMTQzMzVaFw0zNjEw
MjAxMTQzMzVaMBgxFjAUBgNVBAMMDW5ldHByb2JlLXRlc3QwWTATBgcqhkjOPQIB
BggqhkjOPQMBBwNCAATHHTKU1/9ZVH8C+QhEGSwPYEhcvupG6YHnfiFpnKqWhRc7
qS7EK9LC9+xgW4K6FbqrbNRBewBzKd+v5ycqTE13o1MwUTAdBgNVHQ4EFgQUdNo1
EtF2fbcI2o4RrlXHizzg3Y4wHwYDVR0jBBgwFoAUdNo1EtF2fbcI2o4RrlXHizzg
3Y4wDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNIADBFAiEAxxPVk6/tzVA1
CNuxRPPIQQOWj0tvOROxXoVgziSOtAYCIFO1GB+E3EAYICTCADR7inwY08aa84v9
g6n040JKR06K
-----END CERTIFICATE-----
"""
TEST_PIN = "9BXS/GmjzFqVAjtSr+5xnCZFTSL0cMUZ/8UrJAnwxZc="

NOW_MS = 1_760_620_800_000  # Minutenanfang


# --- Reine Funktionen -------------------------------------------------------
def test_parse_ingest_buckets_per_minute():
    body = "\n".join([
        f'wachtl_ping_up{{check_id="2"}} 1 {NOW_MS}',
        f'wachtl_ping_rtt_ms{{check_id="2"}} 12.5 {NOW_MS}',
        f'wachtl_ping_up{{check_id="2"}} 0 {NOW_MS + 5000}',
        f'wachtl_ping_up{{check_id="2"}} 1 {NOW_MS + 10000}',
        f'wachtl_ping_rtt_ms{{check_id="2"}} 7.5 {NOW_MS + 10000}',
        f'wachtl_ping_ttl{{check_id="2"}} 117 {NOW_MS + 10000}',
        f'wachtl_ping_jitter_ms{{check_id="2"}} 1.8 {NOW_MS + 50000}',
        f'wachtl_ping_up{{check_id="2"}} 1 {NOW_MS + 60000}',
    ])
    res = parse_ingest(body, {2}, NOW_MS)
    assert res.rejected == 0
    assert res.accepted == 8
    first = res.buckets[(2, NOW_MS // 1000)]
    assert (first.count, first.loss) == (3, 1)
    assert (first.rtt_min, first.rtt_max, first.rtt_sum, first.rtt_n) == (7.5, 12.5, 20.0, 2)
    assert first.ttl == 117 and first.jitter_n == 1
    assert res.buckets[(2, NOW_MS // 1000 + 60)].count == 1


def test_parse_ingest_rejects():
    old = NOW_MS - 9 * 86400 * 1000
    body = "\n".join([
        "# Kommentar",
        f'wachtl_ping_up{{check_id="9"}} 1 {NOW_MS}',            # fremder Check
        f'wachtl_hop_rtt_ms{{check_id="2",hop="1",hop_ip="10.0.0.1"}} 1 {NOW_MS}',  # Pfad: Phase 3
        f'evil_metric{{check_id="2"}} 1 {NOW_MS}',
        f'wachtl_ping_rtt_ms{{check_id="2"}} NaN {NOW_MS}',
        f'wachtl_ping_rtt_ms{{check_id="2"}} -1 {NOW_MS}',
        f'wachtl_ping_up{{check_id="2"}} 1 {old}',
        f'wachtl_ping_up{{check_id="²"}} 1 {NOW_MS}',
        "kaputt",
    ])
    res = parse_ingest(body, {2}, NOW_MS)
    assert res.accepted == 0
    assert res.reasons == {"check_id": 2, "metric": 2, "value": 2, "timestamp": 1, "format": 1}


def test_parse_ingest_seconds_timestamp():
    res = parse_ingest(f'wachtl_ping_up{{check_id="2"}} 1 {NOW_MS // 1000}', {2}, NOW_MS)
    assert res.accepted == 1


def test_decompress_gzip_limit():
    data = gzip.compress(b"a" * 10_000)
    assert decompress_gzip(data, 20_000) == b"a" * 10_000
    with pytest.raises(BodyTooLarge):
        decompress_gzip(data, 1_000)


def test_spki_pin_matches_openssl():
    assert spki_pin_from_pem(TEST_CERT) == TEST_PIN
    with pytest.raises(ValueError):
        spki_pin_from_pem("kein Zertifikat")


@pytest.mark.parametrize(
    ("count", "loss", "rtt", "expected"),
    [(0, 0, None, "stale"), (10, 10, None, "down"), (10, 2, 5.0, "warn"), (60, 1, 5.0, "ok"),
     (100, 0, 500.0, "warn"), (100, 0, 10.0, "ok")],
)
def test_classify_check(count, loss, rtt, expected):
    assert classify_check(count, loss, rtt, loss_warn_pct=1.0, latency_warn_ms=100.0) == expected


# --- Probe-API v1 -----------------------------------------------------------
@pytest.fixture
def wachtl(admin, tmp_path, monkeypatch):
    """Aktiviert die Anbindung, legt Standort, Ping-Check und Enrollment-Token an."""
    from app.core.config import settings

    cert = tmp_path / "fullchain.pem"
    cert.write_text(TEST_CERT, encoding="ascii")
    monkeypatch.setattr(settings, "wachtl_tls_cert", cert)
    from app.api.wachtl_probe import enroll_rate_limiter

    enroll_rate_limiter._hits.clear()

    assert admin.put("/api/clients/settings", json={"enabled": True}).status_code == 200
    site = admin.post("/api/clients/sites", json={"name": f"Zentrale-{time.time_ns()}"}).json()
    check = admin.post("/api/clients/checks", json={
        "name": "DNS Google", "target": "8.8.8.8", "role": "reference", "interval_s": 5,
        "site_id": site["id"],
    }).json()
    token = admin.post("/api/clients/tokens", json={"site_id": site["id"], "max_uses": 2, "days": 1}).json()
    return {"client": admin, "site": site, "check": check, "token": token["token"]}


def _enroll(client, token: str, hostname: str = "PC-01"):
    return client.post("/api/probe/v1/enroll", json={
        "enrollment_token": token, "hostname": hostname, "os": "Windows 11", "version": "0.9.2"})


def _auth(probe_token: str) -> dict:
    return {"Authorization": f"Bearer {probe_token}"}


def test_settings_show_pin(wachtl):
    s = wachtl["client"].get("/api/clients/settings").json()
    assert s["enabled"] is True and s["pin"] == TEST_PIN and s["pin_error"] is None


def test_check_validation(wachtl):
    c = wachtl["client"]
    assert c.post("/api/clients/checks", json={"name": "x", "target": "-oops"}).status_code == 400
    r = c.post("/api/clients/checks", json={"name": "x", "target": "8.8.4.4", "interval_s": 1})
    assert r.status_code == 400 and "mindestens" in r.json()["detail"]
    assert c.post("/api/clients/checks", json={"name": "GW", "target": "gateway", "role": "gateway"}
                  ).status_code == 201


def test_probe_lifecycle(wachtl):
    c = wachtl["client"]
    assert _enroll(c, "falsch").status_code == 403

    r = _enroll(c, wachtl["token"])
    assert r.status_code == 201, r.text
    enrolled = r.json()
    assert enrolled["site_name"] == wachtl["site"]["name"]
    assert enrolled["token"].startswith(f"{enrolled['probe_id']}.")
    auth = _auth(enrolled["token"])

    # Konfiguration mit ETag
    r = c.get("/api/probe/v1/config", headers=auth)
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["probe_id"] == enrolled["probe_id"]
    ping = next(x for x in cfg["checks"] if x["id"] == wachtl["check"]["id"])
    assert ping == {"id": ping["id"], "type": "ping", "target": "8.8.8.8", "role": "reference",
                    "interval_s": 5, "timeout_ms": 1000,
                    "params": {"size_bytes": 32, "ttl": 128, "jitter_window_s": 60}}
    assert r.headers["etag"] == f'"{cfg["etag"]}"'
    assert c.get("/api/probe/v1/config", headers={**auth, "If-None-Match": f'"{cfg["etag"]}"'}
                 ).status_code == 304

    # Ingest (gzip), Wiederholung desselben Batches zählt nicht doppelt
    cid = wachtl["check"]["id"]
    now_ms = int(time.time() * 1000)
    lines = "\n".join(
        f'wachtl_ping_up{{check_id="{cid}"}} {1 if i % 4 else 0} {now_ms - i * 5000}\n'
        f'wachtl_ping_rtt_ms{{check_id="{cid}"}} {10 + i} {now_ms - i * 5000}'
        for i in range(8)
    )
    hdr = {**auth, "X-Batch-Id": "b-1", "Content-Encoding": "gzip"}
    packed = gzip.compress(lines.encode())
    assert c.post("/api/probe/v1/ingest", content=packed, headers=hdr).status_code == 204
    assert c.post("/api/probe/v1/ingest", content=packed, headers=hdr).status_code == 204
    points = c.get(f"/api/clients/probes/{enrolled['probe_id']}/checks/{cid}/series?range=1h").json()
    assert sum(p["count"] for p in points) == 8
    assert sum(p["loss"] for p in points) == 2

    # Nur unbrauchbare Zeilen → 422; fehlende Batch-ID → 400 mit {"error"}
    r = c.post("/api/probe/v1/ingest", content=f'wachtl_ping_up{{check_id="999"}} 1 {now_ms}',
               headers={**auth, "X-Batch-Id": "b-2"})
    assert r.status_code == 422 and r.json()["reasons"] == {"check_id": 1}
    r = c.post("/api/probe/v1/ingest", content="", headers=auth)
    assert r.status_code == 400 and "error" in r.json()

    # Heartbeat
    r = c.post("/api/probe/v1/heartbeat", headers=auth, json={
        "version": "0.9.2", "os": "Windows 11", "hostname": "PC-01", "buffer_items": 3, "uptime_s": 120,
        "addresses": ["192.168.1.20", "127.0.0.1", "169.254.1.1", "kaputt"]})
    assert r.status_code == 200
    assert r.json()["config_etag"] == cfg["etag"] and r.json()["commands"] == []
    probe = c.get(f"/api/clients/probes/{enrolled['probe_id']}").json()
    assert probe["online"] is True and probe["addresses"] == ["192.168.1.20"] and probe["buffer_items"] == 3

    # Standort-Matrix
    ov = c.get("/api/clients/overview").json()
    site = next(s for s in ov["sites"] if s["site_id"] == wachtl["site"]["id"])
    cell = next(x for x in site["probes"][0]["cells"] if x["check_id"] == cid)
    assert cell["state"] == "warn" and cell["loss_pct"] == 25.0

    # Admin deaktiviert → 403; erneutes Enrollment bleibt gesperrt
    probe_url = f"/api/clients/probes/{enrolled['probe_id']}"
    assert c.patch(probe_url, json={"status": "disabled"}).status_code == 200
    assert c.get("/api/probe/v1/config", headers=auth).status_code == 403
    assert _enroll(c, wachtl["token"]).status_code == 403
    assert c.patch(probe_url, json={"status": "active"}).status_code == 200

    # Abmeldung → Token sofort ungültig, Neuinstallation erlaubt (zweite Token-Nutzung)
    assert c.post("/api/probe/v1/deregister", headers=auth, json={}).status_code == 204
    assert c.get("/api/probe/v1/config", headers=auth).status_code == 401
    r = _enroll(c, wachtl["token"])
    assert r.status_code == 201 and r.json()["probe_id"] == enrolled["probe_id"]
    # Token aufgebraucht (max_uses=2)
    assert _enroll(c, wachtl["token"], "PC-02").status_code == 403


def test_disabled_returns_404(wachtl):
    c = wachtl["client"]
    c.put("/api/clients/settings", json={"enabled": False})
    r = _enroll(c, wachtl["token"])
    assert r.status_code == 404 and "error" in r.json()


def test_viewer_cannot_manage(client):
    assert client.post("/api/clients/sites", json={"name": "x"}).status_code == 401


def test_rollup_to_hours(wachtl):
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.wachtl import WachtlSample
    from app.services import wachtl_service

    c = wachtl["client"]
    enrolled = _enroll(c, wachtl["token"], "PC-ROLL").json()
    pid, cid = enrolled["probe_id"], wachtl["check"]["id"]
    # Volle Stunde vor 20 Tagen (älter als wachtl_minute_retention_days)
    base = (datetime.now(UTC) - timedelta(days=20)).replace(minute=0, second=0, microsecond=0)
    with session_scope() as db:
        for m in range(3):
            db.add(WachtlSample(probe_id=pid, check_id=cid, bucket_ts=base + timedelta(minutes=m), step_s=60,
                                count=12, loss=1, rtt_min=5.0 + m, rtt_max=9.0, rtt_sum=60.0, rtt_n=11,
                                jitter_sum=1.0, jitter_n=1))
    wachtl_service.rollup_and_prune()
    with session_scope() as db:
        rows = list(db.execute(select(WachtlSample).where(WachtlSample.probe_id == pid)).scalars())
    assert len(rows) == 1
    row = rows[0]
    assert (row.step_s, row.count, row.loss, row.rtt_min, row.rtt_n) == (3600, 36, 3, 5.0, 33)
    assert row.bucket_ts == base
