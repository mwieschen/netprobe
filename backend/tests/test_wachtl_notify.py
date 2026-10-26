"""Wachtl-Clients Phase 4: E-Mail-Benachrichtigung (Einstellungen, Empfänger, Lauf, SMTP-Versand)."""

from __future__ import annotations

import socketserver
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services import wachtl_notify
from app.services import wachtl_notify_compose as nc


# --- Minimaler SMTP-Server für den echten Versandweg ------------------------
class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        def say(line: str) -> None:
            self.wfile.write((line + "\r\n").encode())

        say("220 test ESMTP")
        data_mode, lines = False, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if data_mode:
                if line == ".":
                    data_mode = False
                    self.server.messages.append("\n".join(lines))  # type: ignore[attr-defined]
                    lines = []
                    say("250 OK")
                else:
                    lines.append(line)
                continue
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                say("250-test")
                say("250 AUTH PLAIN LOGIN")
            elif cmd == "AUTH":
                ok = self.server.password in line or line.endswith("=")  # type: ignore[attr-defined]
                say("235 OK" if ok else "535 nope")
            elif cmd == "DATA":
                data_mode = True
                say("354 go")
            elif cmd == "QUIT":
                say("221 bye")
                return
            else:
                say("250 OK")


@pytest.fixture
def smtp_server():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SmtpHandler)
    srv.messages = []  # type: ignore[attr-defined]
    srv.password = ""  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


# --- Reine Funktionen -------------------------------------------------------
def test_secret_roundtrip_and_key_change(monkeypatch):
    from app.core.config import settings

    token = wachtl_notify.encrypt_secret("geheim!")
    assert token != "geheim!" and wachtl_notify.decrypt_secret(token) == "geheim!"
    monkeypatch.setattr(settings, "secret_key", "anderer-schluessel")
    assert wachtl_notify.decrypt_secret(token) is None


def test_compose_filter_and_render():
    now = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
    items = [
        nc.Item("incidents", 1, "Köln", "critical", "Ganzer Standort: WAN/Internet", "Provider prüfen", now),
        nc.Item("incidents", 2, "Bonn", "info", "PC-BN-01: Keine Messwerte", "", now),
        nc.Item("recovery", 1, "Köln", "warning", "Vorbei: PC-1 offline", "Dauer etwa 20 min", now),
        nc.Item("events", 1, "Köln", "warning", "PC-1: Pfad geändert", "", now),
    ]
    mine = nc.for_recipient(items, site_id=1, min_severity="warning", categories={"incidents", "recovery"})
    assert [i.title for i in mine] == ["Ganzer Standort: WAN/Internet", "Vorbei: PC-1 offline"]
    subject, body = nc.render(mine, "https://netprobe.local/clients")
    assert subject == "[netprobe] KRITISCH: 1 Störung, 1 Entwarnung – Köln"
    assert "[KRITISCH] 23.10.2026 12:00 · Köln · Ganzer Standort: WAN/Internet" in body
    assert "Details: https://netprobe.local/clients" in body
    assert nc.clean("Böse\r\nBcc: x@y.z") == "Böse  Bcc: x@y.z"
    assert nc.valid_email("it@kunde.de") and not nc.valid_email("a@b") and not nc.valid_email("x\n@y.de")


def test_plan_transitions():
    s = [
        nc.Situation("a", "incident", 1, "warning", "A", "", datetime.now(UTC)),
        nc.Situation("b", "offline", 1, "warning", "B", "", datetime.now(UTC)),
    ]
    started, ended = nc.plan_transitions(s, {"b", "c"})
    assert [x.key for x in started] == ["a"] and ended == {"c"}


# --- API --------------------------------------------------------------------
def test_settings_never_return_password(admin):
    c = admin
    r = c.put(
        "/api/clients/notify/settings",
        json={
            "smtp_host": "mail.kunde.local",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "netprobe",
            "smtp_password": "Sup3r-Geheim",
            "smtp_from": "netprobe@kunde.local",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["password_set"] is True and "smtp_password" not in body and "smtp_password_enc" not in body
    assert "Sup3r-Geheim" not in r.text and "Sup3r-Geheim" not in c.get("/api/clients/notify/settings").text
    audit = c.get("/api/audit?limit=20").text if c.get("/api/audit?limit=20").status_code == 200 else ""
    assert "Sup3r-Geheim" not in audit
    # Passwort unverändert lassen, dann löschen
    assert c.put("/api/clients/notify/settings", json={"smtp_port": 25}).json()["password_set"] is True
    assert c.put("/api/clients/notify/settings", json={"smtp_password": ""}).json()["password_set"] is False
    assert c.put("/api/clients/notify/settings", json={"smtp_from": "kein-mail"}).status_code == 400
    assert c.put("/api/clients/notify/settings", json={"smtp_host": "-x"}).status_code == 400
    assert c.put("/api/clients/notify/settings", json={"smtp_security": "tls13"}).status_code == 422


def test_recipients_crud(admin):
    c = admin
    r = c.post("/api/clients/notify/recipients", json={"name": "IT", "email": " it@kunde.de "})
    assert (
        r.status_code == 201 and r.json()["email"] == "it@kunde.de" and r.json()["min_severity"] == "warning"
    )
    rid = r.json()["id"]
    assert (
        c.post("/api/clients/notify/recipients", json={"name": "x", "email": "a@b\nBcc: c@d.de"}).status_code
        == 400
    )
    assert (
        c.patch(f"/api/clients/notify/recipients/{rid}", json={"min_severity": "critical"}).json()[
            "min_severity"
        ]
        == "critical"
    )
    assert any(x["id"] == rid for x in c.get("/api/clients/notify/recipients").json())
    assert c.delete(f"/api/clients/notify/recipients/{rid}").status_code == 204


def test_notify_requires_admin(client):
    assert client.get("/api/clients/notify/settings").status_code == 401


def test_test_mail_over_real_smtp(admin, smtp_server):
    c = admin
    port = smtp_server.server_address[1]
    c.put(
        "/api/clients/notify/settings",
        json={
            "smtp_host": "127.0.0.1",
            "smtp_port": port,
            "smtp_security": "none",
            "smtp_username": "",
            "smtp_password": "",
            "smtp_from": "netprobe@kunde.local",
        },
    )
    r = c.post("/api/clients/notify/test", json={"to": "it@kunde.de"})
    assert r.status_code == 202, r.text
    assert len(smtp_server.messages) == 1
    msg = smtp_server.messages[0]
    assert "Subject: [netprobe] Testnachricht" in msg and "To: it@kunde.de" in msg
    log = c.get("/api/clients/notify/log").json()
    assert log[0]["ok"] is True and log[0]["recipient"] == "it@kunde.de"
    # Server nicht erreichbar → 502 mit verständlicher Meldung, Eintrag im Protokoll
    c.put("/api/clients/notify/settings", json={"smtp_port": 1})
    r = c.post("/api/clients/notify/test", json={"to": "it@kunde.de"})
    assert r.status_code == 502 and "Versand fehlgeschlagen" in r.json()["detail"]
    assert c.get("/api/clients/notify/log").json()[0]["ok"] is False


# --- Lauf -------------------------------------------------------------------
@pytest.fixture
def scenario(admin, monkeypatch):
    """Standort mit zwei PCs, Gateway- und Referenz-Check; Mails werden abgefangen."""
    from app.api.wachtl_probe import enroll_rate_limiter

    enroll_rate_limiter._hits.clear()
    c = admin
    c.put("/api/clients/settings", json={"enabled": True})
    site = c.post("/api/clients/sites", json={"name": f"N-{time.time_ns()}"}).json()
    gw = c.post(
        "/api/clients/checks",
        json={"name": "GW", "target": "gateway", "role": "gateway", "site_id": site["id"]},
    ).json()
    ref = c.post(
        "/api/clients/checks",
        json={"name": "DNS", "target": "8.8.8.8", "role": "reference", "site_id": site["id"]},
    ).json()
    tok = c.post("/api/clients/tokens", json={"site_id": site["id"]}).json()["token"]
    probes = []
    for h in ("PC-N1", "PC-N2"):
        e = c.post("/api/probe/v1/enroll", json={"enrollment_token": tok, "hostname": h}).json()
        probes.append({"id": e["probe_id"], "auth": {"Authorization": f"Bearer {e['token']}"}})
    c.post(
        "/api/clients/notify/recipients",
        json={"name": "IT", "email": "it@kunde.de", "site_id": site["id"], "min_severity": "warning"},
    )
    c.put(
        "/api/clients/notify/settings",
        json={
            "enabled": True,
            "smtp_host": "127.0.0.1",
            "smtp_port": 25,
            "smtp_security": "none",
            "smtp_from": "netprobe@kunde.local",
            "smtp_password": "",
            "smtp_username": "",
            "max_mails_per_hour": 20,
            "public_url": "https://np.local",
        },
    )
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        wachtl_notify, "send_mail", lambda cfg, to, subject, body: sent.append((to, subject, body))
    )
    # Zustände anderer Tests (gleiche DB) einmal abräumen
    wachtl_notify.run_once()
    sent.clear()
    return {"c": c, "site": site, "gw": gw["id"], "ref": ref["id"], "probes": probes, "sent": sent}


def _ingest(sc, probe, start_ms: int, minutes: int, wan_down: bool, batch: str):
    lines = []
    for m in range(minutes):
        for s in range(0, 60, 5):
            ts = start_ms + m * 60000 + s * 1000
            lines.append(f'wachtl_ping_up{{check_id="{sc["gw"]}"}} 1 {ts}')
            lines.append(f'wachtl_ping_up{{check_id="{sc["ref"]}"}} {0 if wan_down else 1} {ts}')
    r = sc["c"].post(
        "/api/probe/v1/ingest", content="\n".join(lines), headers={**probe["auth"], "X-Batch-Id": batch}
    )
    assert r.status_code == 204


def test_run_incident_open_and_recovery(scenario):
    sc = scenario
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start_ms = int((now - timedelta(minutes=5)).timestamp() * 1000)
    for i, p in enumerate(sc["probes"]):
        _ingest(sc, p, start_ms, 2, False, f"ok-{i}")
        _ingest(sc, p, start_ms + 2 * 60000, 3, True, f"wan-{i}")  # beide PCs: WAN gestört
        sc["c"].post("/api/probe/v1/heartbeat", json={}, headers=p["auth"])

    assert wachtl_notify.run_once(now) == 1
    to, subject, body = sc["sent"][-1]
    assert to == "it@kunde.de" and subject.startswith("[netprobe] KRITISCH: 1 Störung")
    assert "Ganzer Standort: WAN/Internet" in body and "https://np.local/clients" in body
    # Nächster Lauf: nichts Neues → keine Mail
    assert wachtl_notify.run_once(now + timedelta(minutes=1)) == 0
    # Störung vorbei (seit > 3 min keine gestörte Minute mehr) → Entwarnung
    later = now + timedelta(minutes=6)
    for i, p in enumerate(sc["probes"]):
        _ingest(sc, p, int(now.timestamp() * 1000), 6, False, f"back-{i}")
        sc["c"].post("/api/probe/v1/heartbeat", json={}, headers=p["auth"])
    assert wachtl_notify.run_once(later) == 1
    assert "Vorbei: Ganzer Standort: WAN/Internet" in sc["sent"][-1][2]
    log = sc["c"].get("/api/clients/notify/log").json()
    assert log[0]["ok"] is True and log[0]["items"] == 1


def test_run_events_offline_filter_and_rate_limit(scenario):
    from app.db.session import session_scope
    from app.models.wachtl import WachtlEvent, WachtlProbe

    sc = scenario
    now = datetime.now(UTC)
    with session_scope() as db:
        db.add(
            WachtlEvent(
                ts=now,
                site_id=sc["site"]["id"],
                probe_id=sc["probes"][0]["id"],
                kind="mos_low",
                severity="warning",
                title="PC-N1: Sprachqualität mangelhaft",
            )
        )
        db.add(
            WachtlEvent(
                ts=now,
                site_id=sc["site"]["id"],
                probe_id=None,
                kind="path_change",
                severity="info",
                title="PC-N1: Pfad geändert",
            )
        )  # unter min_severity
        db.add(
            WachtlEvent(
                ts=now - timedelta(hours=3),
                site_id=sc["site"]["id"],
                kind="mos_low",
                severity="warning",
                title="alt – wird übersprungen",
            )
        )
        db.get(WachtlProbe, sc["probes"][1]["id"]).last_seen = now - timedelta(minutes=30)
    assert wachtl_notify.run_once(now) == 1
    body = sc["sent"][-1][2]
    assert "Sprachqualität mangelhaft" in body and "PC-N2 offline" in body
    assert "Pfad geändert" not in body and "alt – wird übersprungen" not in body

    # Höchstzahl je Stunde: zweite Mail wird übersprungen und protokolliert
    sc["c"].put("/api/clients/notify/settings", json={"max_mails_per_hour": 1})
    with session_scope() as db:
        db.add(
            WachtlEvent(
                ts=now,
                site_id=sc["site"]["id"],
                kind="bandwidth_low",
                severity="warning",
                title="WAN zu langsam",
            )
        )
    assert wachtl_notify.run_once(now + timedelta(minutes=1)) == 0
    log = sc["c"].get("/api/clients/notify/log").json()
    assert any(x["error"] == "Höchstzahl Mails je Stunde erreicht" and x["items"] == 1 for x in log)


def test_disabled_consumes_without_flood(scenario):
    from app.db.session import session_scope
    from app.models.wachtl import WachtlEvent

    sc = scenario
    sc["c"].put("/api/clients/notify/settings", json={"enabled": False})
    with session_scope() as db:
        db.add(
            WachtlEvent(
                ts=datetime.now(UTC),
                site_id=sc["site"]["id"],
                kind="mos_low",
                severity="warning",
                title="während aus",
            )
        )
    assert wachtl_notify.run_once() == 0
    sc["c"].put("/api/clients/notify/settings", json={"enabled": True})
    assert wachtl_notify.run_once() == 0 and sc["sent"] == []
