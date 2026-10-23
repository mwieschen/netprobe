"""E-Mail-Benachrichtigung der Wachtl-Clients: Einstellungen, SMTP-Versand und der minütliche Lauf.

Einstellungen (SMTP und Grenzwerte) im KV-Store ``wachtl_notify``; das SMTP-Passwort nur verschlüsselt
(Fernet, Schlüssel aus ``NETPROBE_SECRET_KEY`` abgeleitet) und nie über die API zurückgegeben. Empfänger in
``wachtl_recipients``. Der Lauf (``run_once``, Scheduler-Job ``wachtl_notify``) gleicht die aktuellen Lagen
(Störungen aus ``wachtl_analysis``, PCs offline) mit ``wachtl_alert_states`` ab, sammelt neue Ereignisse
(``wachtl_events.notified_at``) und Störungsmeldungen und schickt jedem Empfänger höchstens eine Sammel-Mail.
Ist die Benachrichtigung aus, werden die Zustände trotzdem fortgeschrieben – beim Einschalten kommt keine
Mailflut über Vergangenes.
"""

from __future__ import annotations

import base64
import hashlib
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.wachtl import (
    WachtlAlertState,
    WachtlEvent,
    WachtlNotification,
    WachtlProbe,
    WachtlRecipient,
    WachtlReport,
    WachtlSite,
)
from app.services import kvstore, wachtl_events, wachtl_service
from app.services import wachtl_notify_compose as nc

log = get_logger("netprobe.wachtl.notify")

KV_CONFIG = "wachtl_notify"
DEFAULTS: dict = {
    "enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",  # none | starttls | ssl
    "smtp_username": "",
    "smtp_password_enc": "",
    "smtp_from": "",
    "verify_tls": True,
    "public_url": "",
    "min_incident_minutes": 2,
    "offline_after_min": 15,
    "max_mails_per_hour": 20,
}
EVENT_MAX_AGE = timedelta(hours=1)  # ältere, nie gemeldete Ereignisse/Meldungen werden übersprungen
INCIDENT_WINDOW = timedelta(hours=3)
SMTP_TIMEOUT_S = 20


class NotifyError(RuntimeError):
    pass


# --- Einstellungen ----------------------------------------------------------
def _fernet() -> Fernet:
    digest = hashlib.sha256(f"netprobe-wachtl-smtp:{settings.secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    """None, wenn nicht entschlüsselbar (z. B. ``NETPROBE_SECRET_KEY`` geändert)."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def get_config(db: Session) -> dict:
    stored = kvstore.get_value(db, KV_CONFIG, {}) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def public_config(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k != "smtp_password_enc"}
    enc = cfg["smtp_password_enc"]
    out["password_set"] = bool(enc)
    out["password_error"] = (
        "Gespeichertes Passwort nicht lesbar (NETPROBE_SECRET_KEY geändert?) – bitte neu eingeben."
        if enc and decrypt_secret(enc) is None
        else None
    )
    return out


def update_config(db: Session, changes: dict) -> dict:
    """``smtp_password``: None = unverändert, "" = löschen, sonst neu (verschlüsselt) speichern."""
    cfg = get_config(db)
    password = changes.pop("smtp_password", None)
    for k, v in changes.items():
        if k in DEFAULTS and k != "smtp_password_enc" and v is not None:
            cfg[k] = v
    if password is not None:
        cfg["smtp_password_enc"] = encrypt_secret(password) if password else ""
    kvstore.set_value(db, KV_CONFIG, cfg)
    return cfg


def smtp_ready(cfg: dict) -> str | None:
    """Fehlertext, wenn der Versand so nicht möglich ist, sonst None."""
    if not cfg["smtp_host"]:
        return "SMTP-Server fehlt"
    if not nc.valid_email(cfg["smtp_from"]):
        return "Absenderadresse fehlt oder ist ungültig"
    if cfg["smtp_password_enc"] and decrypt_secret(cfg["smtp_password_enc"]) is None:
        return "Gespeichertes SMTP-Passwort nicht lesbar – bitte neu eingeben"
    return None


# --- Versand ----------------------------------------------------------------
def send_mail(cfg: dict, to: str, subject: str, body: str) -> None:
    """Eine Mail über den konfigurierten SMTP-Server senden. Fehler als ``NotifyError`` (deutsch)."""
    problem = smtp_ready(cfg)
    if problem:
        raise NotifyError(problem)
    if not nc.valid_email(to):
        raise NotifyError(f"Ungültige Empfängeradresse: {to}")
    msg = EmailMessage()
    msg["From"] = formataddr(("netprobe", cfg["smtp_from"]))
    msg["To"] = to
    msg["Subject"] = nc.clean(subject, 250)
    msg["Message-ID"] = make_msgid(domain=cfg["smtp_from"].rsplit("@", 1)[-1])
    msg.set_content(body)
    context = ssl.create_default_context()
    if not cfg["verify_tls"]:
        # Bewusstes Opt-in im UI (Standard: prüfen) für interne Mailserver mit selbstsigniertem Zertifikat –
        # verschlüsselt, aber ohne Schutz vor Man-in-the-Middle. Die Oberfläche warnt, solange es aus ist.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    try:
        if cfg["smtp_security"] == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_S, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S)
        with server:
            server.ehlo()
            if cfg["smtp_security"] == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if cfg["smtp_username"]:
                password = decrypt_secret(cfg["smtp_password_enc"]) if cfg["smtp_password_enc"] else ""
                server.login(cfg["smtp_username"], password or "")
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise NotifyError("SMTP-Anmeldung abgelehnt (Benutzer/Passwort prüfen)") from exc
    except ssl.SSLError as exc:
        raise NotifyError(f"TLS-Fehler zum SMTP-Server: {exc.reason or exc}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"Versand fehlgeschlagen: {exc}") from exc


# --- Lagen erfassen ---------------------------------------------------------
def _situations(
    db: Session, cfg: dict, now: datetime, open_states: list[WachtlAlertState]
) -> list[nc.Situation]:
    hosts = {p.id: p.hostname for p in db.execute(select(WachtlProbe)).scalars()}
    out: list[nc.Situation] = []
    # Störungen: Fenster reicht bis zum Beginn der ältesten offenen Störung, damit ihr Schlüssel stabil bleibt
    oldest = min((s.opened_at for s in open_states if s.kind == "incident"), default=now)
    since = max(
        min(now - INCIDENT_WINDOW, oldest - timedelta(minutes=5)),
        now - timedelta(days=settings.wachtl_incident_max_days),
    )
    for inc in wachtl_service.incidents(db, since, now):
        ongoing = (now - datetime.fromtimestamp(inc["end"], UTC)).total_seconds() < 180
        if not ongoing or inc["minutes"] < int(cfg["min_incident_minutes"]):
            continue
        who = "Ganzer Standort" if inc["scope"] == "site" else hosts.get(inc["probe_ids"][0], "?")
        detail = inc["hint"]
        if inc["max_loss_pct"] is not None:
            detail += f"\nHöchster Paketverlust: {inc['max_loss_pct']:.0f} %"
        out.append(
            nc.Situation(
                key=nc.incident_key(inc),
                kind="incident",
                site_id=inc["site_id"],
                severity=nc.incident_severity(inc),
                title=f"{who}: {inc['cause_label']}",
                detail=detail,
                since=datetime.fromtimestamp(inc["start"], UTC),
            )
        )
    # PCs offline (aktiv, nicht abgemeldet, seit offline_after_min kein Kontakt)
    limit = now - timedelta(minutes=int(cfg["offline_after_min"]))
    for p in db.execute(
        select(WachtlProbe).where(
            WachtlProbe.status == "active",
            WachtlProbe.deregistered_at.is_(None),
            WachtlProbe.last_seen.is_not(None),
            WachtlProbe.last_seen < limit,
        )
    ).scalars():
        assert p.last_seen is not None
        out.append(
            nc.Situation(
                key=f"offline:{p.id}",
                kind="offline",
                site_id=p.site_id,
                severity="warning",
                title=f"{p.hostname} offline",
                detail=f"Letzter Kontakt {nc._fmt(p.last_seen)}",
                since=p.last_seen,
            )
        )
    return out


def collect(db: Session, cfg: dict, now: datetime) -> list[nc.Item]:
    """Zustände fortschreiben und die neuen Einträge dieses Laufs liefern."""
    sites = {s.id: s.name for s in db.execute(select(WachtlSite)).scalars()}
    items: list[nc.Item] = []
    open_states = list(
        db.execute(
            select(WachtlAlertState).where(
                WachtlAlertState.kind.in_(("incident", "offline")), WachtlAlertState.closed_at.is_(None)
            )
        ).scalars()
    )
    current = _situations(db, cfg, now, open_states)
    started, ended = nc.plan_transitions(current, {s.key for s in open_states})
    for sit in started:
        previous = db.get(WachtlAlertState, sit.key)
        if previous is not None:
            # Schon einmal beendet und mit gleichem Beginn wieder aktiv (Episode lief nach Pause weiter):
            # Zustand wieder öffnen statt doppelt anlegen.
            previous.closed_at = None
            previous.notified_close_at = None
            previous.notified_open_at = now
            previous.severity, previous.title, previous.detail = sit.severity, sit.title[:300], sit.detail
        else:
            db.add(
                WachtlAlertState(
                    key=sit.key,
                    kind=sit.kind,
                    site_id=sit.site_id,
                    severity=sit.severity,
                    title=sit.title[:300],
                    detail=sit.detail,
                    opened_at=sit.since,
                    notified_open_at=now,
                )
            )
        items.append(
            nc.Item(
                "incidents",
                sit.site_id,
                sites.get(sit.site_id, "?"),
                sit.severity,
                sit.title,
                sit.detail,
                sit.since,
            )
        )
    for st in open_states:
        if st.key in ended:
            st.closed_at = now
            st.notified_close_at = now
            minutes = max(1, int((now - st.opened_at).total_seconds() // 60))
            items.append(
                nc.Item(
                    "recovery",
                    st.site_id,
                    sites.get(st.site_id, "?"),
                    st.severity,
                    f"Vorbei: {st.title}",
                    f"Dauer etwa {minutes} min",
                    now,
                )
            )
    # Ereignisse (Pfadwechsel, Bandbreite/MOS …)
    for e in db.execute(select(WachtlEvent).where(WachtlEvent.notified_at.is_(None))).scalars():
        e.notified_at = now
        if now - e.ts <= EVENT_MAX_AGE:
            items.append(
                nc.Item(
                    "events", e.site_id, sites.get(e.site_id, "?"), e.severity, e.title, e.detail or "", e.ts
                )
            )
    # Störungsmeldungen der Benutzer (Tray „Problem melden“)
    for r in db.execute(
        select(WachtlReport).where(WachtlReport.received_at >= now - EVENT_MAX_AGE)
    ).scalars():
        key = f"report:{r.id}"
        if db.get(WachtlAlertState, key) is not None:
            continue
        title = f"{r.hostname}: {wachtl_events.REPORT_CATEGORIES.get(r.category, r.category)}"
        detail = "\n".join(
            x
            for x in (
                f"Dauer: {wachtl_events.REPORT_DURATIONS.get(r.duration, r.duration)}",
                f"Benutzer: {r.reported_user}" if r.reported_user else "",
                r.description or "",
            )
            if x
        )
        db.add(
            WachtlAlertState(
                key=key,
                kind="report",
                site_id=r.site_id,
                severity="warning",
                title=title[:300],
                detail=detail,
                opened_at=r.occurred_at,
                closed_at=now,
                notified_open_at=now,
            )
        )
        items.append(
            nc.Item("reports", r.site_id, sites.get(r.site_id, "?"), "warning", title, detail, r.occurred_at)
        )
    return items


def _recipient_categories(r: WachtlRecipient) -> set[str]:
    return {
        c
        for c, on in (
            ("incidents", r.notify_incidents),
            ("recovery", r.notify_recovery),
            ("events", r.notify_events),
            ("reports", r.notify_reports),
        )
        if on
    }


# --- Lauf -------------------------------------------------------------------
def run_once(now: datetime | None = None) -> int:
    """Ein Benachrichtigungslauf. → Anzahl versendeter Mails."""
    now = now or utcnow()
    with session_scope() as db:
        if not wachtl_service.is_enabled(db):
            return 0
        cfg = get_config(db)
        items = collect(db, cfg, now)
        if not items or not cfg["enabled"] or smtp_ready(cfg):
            if items and cfg["enabled"]:
                log.warning("Benachrichtigung nicht möglich: %s", smtp_ready(cfg))
            return 0
        hour_ago = now - timedelta(hours=1)
        plan: list[tuple[str, str, str, int]] = []
        for r in db.execute(select(WachtlRecipient).where(WachtlRecipient.enabled.is_(True))).scalars():
            mine = nc.for_recipient(
                items, site_id=r.site_id, min_severity=r.min_severity, categories=_recipient_categories(r)
            )
            if not mine:
                continue
            sent = (
                db.execute(
                    select(func.count())
                    .select_from(WachtlNotification)
                    .where(
                        WachtlNotification.recipient == r.email,
                        WachtlNotification.ts >= hour_ago,
                        WachtlNotification.ok.is_(True),
                    )
                ).scalar()
                or 0
            )
            if sent >= int(cfg["max_mails_per_hour"]):
                db.add(
                    WachtlNotification(
                        ts=now,
                        recipient=r.email,
                        subject="(übersprungen)",
                        items=len(mine),
                        ok=False,
                        error="Höchstzahl Mails je Stunde erreicht",
                    )
                )
                continue
            subject, body = nc.render(
                mine, f"{cfg['public_url'].rstrip('/')}/clients" if cfg["public_url"] else None
            )
            plan.append((r.email, subject, body, len(mine)))
    # Versand außerhalb der DB-Transaktion (SMTP kann dauern)
    results = []
    for to, subject, body, n in plan:
        try:
            send_mail(cfg, to, subject, body)
            results.append(WachtlNotification(ts=now, recipient=to, subject=subject, items=n, ok=True))
        except NotifyError as exc:
            log.warning("Benachrichtigung an %s fehlgeschlagen: %s", to, exc)
            results.append(
                WachtlNotification(
                    ts=now, recipient=to, subject=subject, items=n, ok=False, error=str(exc)[:1000]
                )
            )
    if results:
        with session_scope() as db:
            db.add_all(results)
    sent_ok = sum(1 for r in results if r.ok)
    if sent_ok:
        log.info("Benachrichtigung: %d Mail(s) versendet (%d Einträge)", sent_ok, len(items))
    return sent_ok


def test_mail(db: Session, to: str) -> None:
    cfg = get_config(db)
    subject = "[netprobe] Testnachricht"
    body = (
        "Diese Testnachricht bestätigt, dass netprobe über den eingestellten SMTP-Server "
        "Mails versenden kann.\n\n"
        f"SMTP-Server: {cfg['smtp_host']}:{cfg['smtp_port']} ({cfg['smtp_security']})\n"
        f"Absender: {cfg['smtp_from']}\n"
    )
    try:
        send_mail(cfg, to, subject, body)
    except NotifyError as exc:
        db.add(WachtlNotification(recipient=to, subject=subject, items=0, ok=False, error=str(exc)[:1000]))
        raise
    db.add(WachtlNotification(recipient=to, subject=subject, items=0, ok=True))


def prune(db: Session) -> None:
    cutoff = utcnow() - timedelta(days=settings.wachtl_event_retention_days)
    db.query(WachtlNotification).filter(WachtlNotification.ts < cutoff).delete(synchronize_session=False)
    db.query(WachtlAlertState).filter(
        WachtlAlertState.closed_at.is_not(None), WachtlAlertState.closed_at < cutoff
    ).delete(synchronize_session=False)
