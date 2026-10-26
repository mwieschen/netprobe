"""Störungsmeldungen und Client-Hinweise der Wachtl-Probes: Prüfung und Normalisierung (rein, ohne DB).

Vertrag wie Wachtl 0.9 (``server/wachtl/reports.py``, ``server/wachtl/findings.py``,
``docs/STOERUNGSMELDUNGEN.md``, ``docs/CLIENT-HINWEISE.md``). Die Texte für Art/Dauer/Hinweis-Art
stehen nur hier; die Fehlermeldungen gehen als ``400 {"error": …}`` an die Probe.
"""

from __future__ import annotations

import re

REPORT_CATEGORIES = {
    "no_connection": "Keine Verbindung / Internet weg",
    "slow": "Langsam / lange Ladezeiten",
    "dropouts": "Aussetzer / Verbindungsabbrüche",
    "voice_video": "Telefonie oder Video gestört",
    "application": "Bestimmte Anwendung oder Website betroffen",
    "other": "Sonstiges",
}
REPORT_DURATIONS = {
    "unknown": "Unbekannt",
    "short": "Kurz (unter 1 Minute)",
    "minutes": "Einige Minuten (1–15 Minuten)",
    "long": "Länger (über 15 Minuten)",
    "ongoing": "Dauert noch an",
}
FINDING_KINDS = {
    "cable_unplugged": "Netzwerkkabel getrennt",
    "wifi_disconnected": "WLAN getrennt",
    "link_speed_changed": "Link-Geschwindigkeit geändert",
    "link_speed_low": "Link-Geschwindigkeit niedrig",
    "link_flapping": "Verbindung instabil",
    "driver_error": "Treiber- oder Gerätefehler",
    "nic_reset": "Netzwerkkarte wird zurückgesetzt",
    "apipa": "Keine Adresse vom DHCP-Server",
    "no_gateway": "Kein Standard-Gateway",
    "nic_errors": "Übertragungsfehler der Netzwerkkarte",
}
FINDING_SEVERITIES = ("info", "warning", "critical")
FINDING_STATES = ("active", "resolved")

REPORT_MAX_AGE_S = 7 * 86400
FINDING_MAX_AGE_S = 30 * 86400
MAX_FUTURE_S = 300
FINDINGS_MAX_BATCH = 50
MAX_DESCRIPTION, MAX_USER, MAX_HOSTNAME = 1000, 128, 255
MAX_TITLE, MAX_TEXT, MAX_ADAPTER, MAX_DETAILS, MAX_DETAIL_VALUE = 120, 500, 255, 20, 200

ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EventError(ValueError):
    """Ungültige Meldung/Hinweis; der Text geht an die Probe."""


def _text(value, limit: int) -> str:
    # Steuerzeichen entfernen, Zeilenumbrüche behalten
    return _CONTROL.sub("", str(value if value is not None else "")).strip()[:limit]


def _ts(value, name: str) -> int:
    if isinstance(value, bool):
        raise EventError(f"{name} ist keine Unix-Zeit.")
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        raise EventError(f"{name} fehlt oder ist keine Unix-Zeit in Sekunden.") from None


def validate_report(data, now: int) -> dict:
    """Körper von ``POST /api/probe/v1/report`` prüfen und normalisieren."""
    if not isinstance(data, dict):
        raise EventError("JSON-Objekt erwartet.")
    report_id = data.get("report_id")
    if not isinstance(report_id, str) or not ID_RE.match(report_id.strip()):
        raise EventError("report_id fehlt oder ist ungültig (UUID erwartet).")
    occurred_at = _ts(data.get("occurred_at"), "occurred_at")
    if not now - REPORT_MAX_AGE_S <= occurred_at <= now + MAX_FUTURE_S:
        raise EventError("occurred_at muss zwischen jetzt − 7 Tagen und jetzt + 5 Minuten liegen.")
    category = data.get("category")
    if category not in REPORT_CATEGORIES:
        raise EventError("category ungültig – erlaubt: " + ", ".join(REPORT_CATEGORIES) + ".")
    duration = data.get("duration") or "unknown"
    if duration not in REPORT_DURATIONS:
        raise EventError("duration ungültig – erlaubt: " + ", ".join(REPORT_DURATIONS) + ".")
    return {
        "report_id": report_id.strip().lower(),
        "occurred_at": occurred_at,
        "category": category,
        "duration": duration,
        "description": _text(data.get("description"), MAX_DESCRIPTION),
        "user": _text(data.get("user"), MAX_USER),
        "hostname": _text(data.get("hostname"), MAX_HOSTNAME),
    }


def validate_finding(item, now: int) -> dict:
    """Einen Client-Hinweis prüfen. Unbekannte Arten sind erlaubt (neuere Probe-Version)."""
    if not isinstance(item, dict):
        raise EventError("JSON-Objekt erwartet.")
    fid = item.get("finding_id")
    if not isinstance(fid, str) or not ID_RE.match(fid.strip()):
        raise EventError("finding_id fehlt oder ist ungültig.")
    kind = item.get("kind")
    if not isinstance(kind, str) or not KIND_RE.match(kind):
        raise EventError("kind fehlt oder ist ungültig.")
    severity = item.get("severity")
    if severity not in FINDING_SEVERITIES:
        raise EventError("severity ungültig – erlaubt: " + ", ".join(FINDING_SEVERITIES) + ".")
    state = item.get("state")
    if state not in FINDING_STATES:
        raise EventError("state ungültig – erlaubt: active, resolved.")
    detected_at = _ts(item.get("detected_at"), "detected_at")
    if not now - FINDING_MAX_AGE_S <= detected_at <= now + MAX_FUTURE_S:
        raise EventError("detected_at muss zwischen jetzt − 30 Tagen und jetzt + 5 Minuten liegen.")
    resolved_at = None
    if state == "resolved":
        resolved_at = _ts(item.get("resolved_at") or now, "resolved_at")
        resolved_at = min(max(resolved_at, detected_at), now + MAX_FUTURE_S)
    details = item.get("details") or {}
    if not isinstance(details, dict):
        raise EventError("details muss ein JSON-Objekt sein.")
    clean = {_text(k, 40): _text(v, MAX_DETAIL_VALUE) for k, v in list(details.items())[:MAX_DETAILS]}
    return {
        "finding_id": fid.strip(), "kind": kind, "severity": severity, "state": state,
        "title": _text(item.get("title"), MAX_TITLE) or FINDING_KINDS.get(kind, kind),
        "message": _text(item.get("message"), MAX_TEXT), "solution": _text(item.get("solution"), MAX_TEXT),
        "adapter": _text(item.get("adapter"), MAX_ADAPTER),
        "adapter_description": _text(item.get("adapter_description"), MAX_ADAPTER),
        "details": clean, "detected_at": detected_at, "resolved_at": resolved_at,
    }


def validate_findings_batch(data, now: int) -> tuple[list[dict], list[str]]:
    """Körper von ``POST /api/probe/v1/findings``. Ungültige Einträge werden einzeln übersprungen."""
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise EventError('JSON-Objekt mit Liste "findings" erwartet.')
    items = data["findings"]
    if len(items) > FINDINGS_MAX_BATCH:
        raise EventError(f"Höchstens {FINDINGS_MAX_BATCH} Hinweise je Sendung.")
    valid: list[dict] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        try:
            valid.append(validate_finding(item, now))
        except EventError as exc:
            errors.append(f"Eintrag {i + 1}: {exc}")
    return valid, errors
