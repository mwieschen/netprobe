"""Ausfälle der Wachtl-Clients mit wahrscheinlicher Ursache (rein, ohne DB).

Grundlage sind die 1-Minuten-Buckets je Probe und Check. Je Probe und Minute:

- **lan**    – ein Check mit Rolle ``gateway`` ist gestört (Gateway/LAN des PCs)
- **wan**    – Gateway erreichbar, aber *alle* Internet-Referenzen gestört (Anschluss/Provider)
- **target** – nur einzelne Ziele/Referenzen gestört (Zielsystem oder Weg dorthin)
- **probe**  – Lücke ohne jede Messung zwischen zwei Minuten mit Daten (PC aus, Dienst gestoppt, Kabel)

Sind in einer Minute *alle* (mindestens zwei) PCs eines Standorts mit derselben Ursache lan/wan/probe
betroffen, zählt die Minute als Störung des Standorts; die Einzelstörungen dieser Minute entfallen
(Folgestörungen zusammengefasst).

Gestörte Minuten werden zu **Episoden** verbunden, solange höchstens ``merge_gap_min`` ungestörte Minuten
dazwischen liegen – sonst zerfällt eine halbe Stunde mit schwankendem Paketverlust in Dutzende
Einzelstörungen, die zwischen WAN und Ziel hin- und herspringen. Ursache einer Episode ist die
häufigste ihrer Minuten (bei Gleichstand lan vor wan vor target).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MINUTE = 60

CAUSES: dict[str, tuple[str, str]] = {
    "lan": ("Gateway/LAN", "Default-Gateway nicht erreichbar – Netzwerkkabel, Switch, WLAN oder Router am "
                            "Standort prüfen."),
    "wan": ("WAN/Internet", "Gateway erreichbar, aber alle Internet-Referenzen gestört – Internetanschluss, "
                            "Router-WAN oder Provider prüfen."),
    "target": ("Ziel", "Nur einzelne Ziele gestört, Gateway und Internet in Ordnung – Zielsystem oder "
                       "den Weg dorthin (Firewall, VPN, Routing) prüfen."),
    "probe": ("Keine Messwerte", "Der PC hat in dieser Zeit nichts gemessen – ausgeschaltet, Wachtl-Dienst "
                                 "gestoppt oder Netzwerk am PC getrennt."),
}
_SEVERITY = {"lan": 0, "wan": 1, "target": 2, "probe": 3}


@dataclass
class Episode:
    scope: str                       # "site" oder "probe"
    site_id: int
    probe_ids: list[int]
    start: int                       # Unix-Sekunden, Minutenanfang
    end: int                         # exklusiv
    cause_minutes: dict[str, int] = field(default_factory=dict)
    check_ids: set[int] = field(default_factory=set)
    max_loss_pct: float | None = None

    def add(self, minute: int, cause: str, check_ids: set[int], loss: float | None) -> None:
        self.end = minute + MINUTE
        self.cause_minutes[cause] = self.cause_minutes.get(cause, 0) + 1
        self.check_ids |= check_ids
        if loss is not None:
            self.max_loss_pct = max(self.max_loss_pct or 0.0, round(loss, 1))

    @property
    def cause(self) -> str:
        return min(self.cause_minutes, key=lambda c: (-self.cause_minutes[c], _SEVERITY[c]))

    def as_dict(self) -> dict:
        cause = self.cause
        label, hint = CAUSES[cause]
        checks = sorted(self.check_ids) if cause == "target" else []
        return {
            "scope": self.scope, "cause": cause, "cause_label": label, "hint": hint,
            "site_id": self.site_id, "probe_ids": self.probe_ids,
            "check_id": checks[0] if len(checks) == 1 else None, "check_ids": checks,
            "start": self.start, "end": self.end, "minutes": (self.end - self.start) // MINUTE,
            "affected_minutes": sum(self.cause_minutes.values()), "max_loss_pct": self.max_loss_pct,
        }


def _probe_minute(
    checks: dict[int, str], data: dict[int, tuple[int, int]], threshold_pct: float,
) -> tuple[str, set[int], float] | None:
    """Ursache einer Probe in einer Minute → (cause, gestörte Checks, höchster Verlust) oder None."""

    def loss_pct(cid: int) -> float:
        cnt, loss = data[cid]
        return loss * 100.0 / cnt

    present = [cid for cid in data if cid in checks and data[cid][0] > 0]
    bad = {cid for cid in present if loss_pct(cid) >= threshold_pct}
    if not bad:
        return None
    worst = max(loss_pct(c) for c in bad)
    if any(checks[c] == "gateway" for c in bad):
        return "lan", set(), worst
    refs = [c for c in present if checks[c] == "reference"]
    if refs and all(c in bad for c in refs):
        return "wan", set(), worst
    return "target", bad, worst


def detect_incidents(
    site_id: int,
    probe_ids: list[int],
    checks: dict[int, str],
    buckets: dict[tuple[int, int, int], tuple[int, int]],
    *,
    threshold_pct: float,
    min_interval_s: int = 60,
    merge_gap_min: int = 2,
) -> list[dict]:
    """Störungen eines Standorts.

    :param checks: check_id → Rolle (``target``/``gateway``/``reference``) der für den Standort aktiven Checks
    :param buckets: (probe_id, check_id, minute) → (count, loss); minute = Unix-Sekunden, auf 60 s gerundet
    :param min_interval_s: kleinstes Check-Intervall; Lücken erst ab zwei Intervallen (mind. 2 min) zählen
    :param merge_gap_min: so viele ungestörte Minuten dürfen innerhalb einer Episode liegen
    """
    per_probe: dict[int, dict[int, dict[int, tuple[int, int]]]] = {p: {} for p in probe_ids}
    for (pid, cid, minute), val in buckets.items():
        if pid in per_probe:
            per_probe[pid].setdefault(minute, {})[cid] = val

    # Minute → Probe → (cause, Checks, Verlust)
    minutes: dict[int, dict[int, tuple[str, set[int], float | None]]] = {}
    gap_min = max(2, math.ceil(2 * min_interval_s / MINUTE))
    for pid, by_minute in per_probe.items():
        ordered = sorted(by_minute)
        for m in ordered:
            found = _probe_minute(checks, by_minute[m], threshold_pct)
            if found is not None:
                minutes.setdefault(m, {})[pid] = found
        for a, b in zip(ordered, ordered[1:], strict=False):
            if (b - a) // MINUTE - 1 >= gap_min:
                for m in range(a + MINUTE, b, MINUTE):
                    minutes.setdefault(m, {})[pid] = ("probe", set(), None)

    open_: dict[tuple, Episode] = {}
    done: list[Episode] = []
    max_gap_s = merge_gap_min * MINUTE

    def add(key: tuple, minute: int, scope: str, probes: list[int], cause: str, cids: set[int],
            loss: float | None) -> None:
        ep = open_.get(key)
        if ep is not None and minute - ep.end > max_gap_s:
            done.append(ep)
            ep = None
        if ep is None:
            ep = open_[key] = Episode(scope=scope, site_id=site_id, probe_ids=probes,
                                      start=minute, end=minute)
        ep.add(minute, cause, cids, loss)

    for m in sorted(minutes):
        by_probe = minutes[m]
        kinds = {cause for cause, _c, _l in by_probe.values()}
        if (len(probe_ids) >= 2 and set(by_probe) == set(probe_ids) and len(kinds) == 1
                and next(iter(kinds)) in ("lan", "wan", "probe")):
            cause = next(iter(kinds))
            losses = [loss for _c, _ids, loss in by_probe.values() if loss is not None]
            add(("site", cause), m, "site", sorted(probe_ids), cause, set(), max(losses) if losses else None)
            continue
        for pid, (cause, cids, loss) in by_probe.items():
            # Messlücken bleiben eigene Episoden, Störungen mit Messwerten werden je PC zusammengefasst.
            key = ("probe", pid, "probe" if cause == "probe" else "degraded")
            add(key, m, "probe", [pid], cause, cids, loss)

    done.extend(open_.values())
    done.sort(key=lambda e: (e.start, e.scope == "site"), reverse=True)
    return [e.as_dict() for e in done]
