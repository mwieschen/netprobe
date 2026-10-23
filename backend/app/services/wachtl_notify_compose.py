"""E-Mail-Benachrichtigung der Wachtl-Clients: reine Logik (ohne DB/SMTP).

- Welche Störungen/Offline-PCs neu sind und welche vorbei (``plan_transitions``)
- Welche Einträge ein Empfänger bekommt (``for_recipient``: Standort, Mindest-Schwere, Art)
- Betreff und Text der Sammel-Mail (``render``)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_LABEL = {"info": "Info", "warning": "Warnung", "critical": "KRITISCH"}
# Kategorien, die ein Empfänger einzeln an-/abwählt
CATEGORIES = ("incidents", "recovery", "events", "reports")
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
TZ = ZoneInfo("Europe/Berlin")


def valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(EMAIL_RE.match(value))


def clean(text: str, limit: int = 300) -> str:
    """Für Betreff/Kopfzeilen: keine Steuerzeichen (Header-Injection), gekürzt."""
    return _CONTROL.sub(" ", text).strip()[:limit]


@dataclass(frozen=True)
class Item:
    category: str  # incidents | recovery | events | reports
    site_id: int
    site_name: str
    severity: str  # info | warning | critical
    title: str
    detail: str
    ts: datetime


@dataclass(frozen=True)
class Situation:
    """Eine aktuell bestehende Lage (Störung oder PC offline) mit stabilem Schlüssel."""

    key: str
    kind: str  # incident | offline
    site_id: int
    severity: str
    title: str
    detail: str
    since: datetime


def incident_severity(inc: dict) -> str:
    """Standort-weite Störungen von Gateway/WAN sind kritisch, einzelne PCs eine Warnung,
    Messlücken einzelner PCs nur Info (oft ein ausgeschalteter PC)."""
    if inc["cause"] == "probe":
        return "warning" if inc["scope"] == "site" else "info"
    if inc["scope"] == "site" and inc["cause"] in ("lan", "wan"):
        return "critical"
    return "warning"


def incident_key(inc: dict) -> str:
    # Ohne Ursache: die vorherrschende Ursache einer Episode kann sich noch ändern, der Beginn nicht.
    return f"inc:{inc['site_id']}:{inc['scope']}:{','.join(map(str, inc['probe_ids']))}:{inc['start']}"


def plan_transitions(current: list[Situation], open_keys: set[str]) -> tuple[list[Situation], set[str]]:
    """→ (neu begonnene Lagen, beendete Schlüssel)."""
    cur = {s.key for s in current}
    return [s for s in current if s.key not in open_keys], open_keys - cur


def for_recipient(
    items: list[Item], *, site_id: int | None, min_severity: str, categories: set[str]
) -> list[Item]:
    floor = SEVERITY_RANK.get(min_severity, 1)
    return [
        i
        for i in items
        if (site_id is None or i.site_id == site_id)
        and i.category in categories
        and (i.category == "recovery" or SEVERITY_RANK.get(i.severity, 0) >= floor)
    ]


def _fmt(ts: datetime) -> str:
    return ts.astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def render(items: list[Item], link: str | None = None) -> tuple[str, str]:
    """Betreff und Text (Klartext) der Sammel-Mail."""
    counts = {c: sum(1 for i in items if i.category == c) for c in CATEGORIES}
    parts = []
    if counts["incidents"]:
        parts.append(f"{counts['incidents']} Störung{'en' if counts['incidents'] > 1 else ''}")
    if counts["recovery"]:
        parts.append(f"{counts['recovery']} Entwarnung{'en' if counts['recovery'] > 1 else ''}")
    if counts["events"]:
        parts.append(f"{counts['events']} Ereignis{'se' if counts['events'] > 1 else ''}")
    if counts["reports"]:
        parts.append(f"{counts['reports']} Meldung{'en' if counts['reports'] > 1 else ''}")
    sites = sorted({i.site_name for i in items})
    worst = max((SEVERITY_RANK[i.severity] for i in items if i.category != "recovery"), default=-1)
    prefix = "[netprobe] KRITISCH: " if worst == 2 else "[netprobe] "
    subject = clean(prefix + ", ".join(parts) + " – " + ", ".join(sites), 250)

    headings = {
        "incidents": "Neue Störungen",
        "recovery": "Entwarnungen",
        "events": "Ereignisse",
        "reports": "Störungsmeldungen von Benutzern",
    }
    lines = ["netprobe – Benachrichtigung der Wachtl-Clients", ""]
    for cat in CATEGORIES:
        group = sorted(
            (i for i in items if i.category == cat), key=lambda i: (-SEVERITY_RANK[i.severity], i.ts)
        )
        if not group:
            continue
        lines += [headings[cat], "-" * len(headings[cat])]
        for i in group:
            label = "Entwarnung" if cat == "recovery" else SEVERITY_LABEL[i.severity]
            lines.append(f"[{label}] {_fmt(i.ts)} · {i.site_name} · {i.title}")
            if i.detail:
                lines += [f"    {line}" for line in i.detail.splitlines()]
        lines.append("")
    if link:
        lines += [f"Details: {link}", ""]
    lines.append(
        "Diese Mail wurde automatisch von netprobe erzeugt. Empfänger und Einstellungen: "
        "Clients (Wachtl) → Benachrichtigung."
    )
    return subject, "\n".join(lines)
