"""Ergebnisse geplanter Messungen der Wachtl-Clients (rein, ohne DB): Pfad, Bandbreite, Sprache.

Prüfung wie Wachtl ``api_probe.result_path/_bandwidth/_rtp``; dazu die Bewertung der Hop-Tabelle
(MTR-Ansicht) und die Ereignisse, die eine Messung auslöst (Pfadwechsel, Bandbreite/MOS unter
Schwellwert, Messfehler) – Grundlage der Benachrichtigung.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass

MAX_HOPS = 64
MAX_RESULT_AGE_S = 8 * 86400
MAX_FUTURE_S = 300
# MTR-Bewertung
HOP_LOSS_PCT = 1.0  # ab hier gilt ein Hop als verlustbehaftet
LATENCY_JUMP_MS = 30.0  # Anstieg gegenüber dem vorigen Hop, der bis zum Ziel bleibt
LATENCY_KEEP_MS = 10.0  # Toleranz: spätere Hops dürfen so viel schneller sein


class ResultError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    kind: str
    severity: str  # "info" | "warning" | "critical"
    title: str
    detail: str = ""


def result_ts(raw, now: int) -> int:
    """Zeitstempel der Messung (Sekunden oder ms); unplausible Werte → jetzt."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return now
    if value > 10**12:
        value //= 1000
    return value if now - MAX_RESULT_AGE_S <= value <= now + MAX_FUTURE_S else now


def num(value, lo: float, hi: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _count(value) -> int | None:
    v = num(value, 0, 10**7)
    return None if v is None else int(v)


# --- Pfad -------------------------------------------------------------------
def hop_list(raw) -> list[dict]:
    hops: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            hop = int(str(item.get("hop")))
        except (TypeError, ValueError):
            continue
        if not 1 <= hop <= MAX_HOPS or any(h["hop"] == hop for h in hops):
            continue
        ip = None
        if item.get("ip"):
            try:
                ip = str(ipaddress.ip_address(str(item["ip"])))
            except ValueError:
                ip = None
        hops.append({"hop": hop, "ip": ip, "rtt_ms": num(item.get("rtt_ms"), 0, 60000) if ip else None})
    hops.sort(key=lambda h: h["hop"])
    return hops


def path_hash(hops: list[dict]) -> str:
    return hashlib.sha256(json.dumps([[h["hop"], h["ip"]] for h in hops]).encode()).hexdigest()[:16]


def path_differs(old: list[dict], new: list[dict]) -> bool:
    """Pfad gewechselt? Hops ohne Antwort zählen nicht als Wechsel (ICMP-Drosselung)."""
    old_map = {h["hop"]: h["ip"] for h in old}
    for h in new:
        before = old_map.get(h["hop"])
        if h["ip"] and before and h["ip"] != before:
            return True
    last_old = max((h["hop"] for h in old if h["ip"]), default=0)
    last_new = max((h["hop"] for h in new if h["ip"]), default=0)
    return bool(last_old and last_new and last_old != last_new)


def fill_known(old: list[dict], new: list[dict]) -> list[dict]:
    """Gleicher Pfad: Hops, die diesmal nicht geantwortet haben, behalten ihre bekannte Adresse."""
    known = {h["hop"]: h["ip"] for h in old if h["ip"]}
    return [
        h if h["ip"] or h["hop"] not in known else {**h, "ip": known[h["hop"]], "stale": True} for h in new
    ]


def evaluate_hops(rows: list[dict]) -> list[dict]:
    """MTR-Bewertung je Hop.

    ``rows``: je Hop ``{hop, ip, count, loss, rtt_avg, …}`` aufsteigend. Ergebnis je Hop zusätzlich
    ``loss_pct``, ``verdict`` und ``verdict_label``:

    - ``icmp_limit`` – Verlust nur an diesem Hop, spätere Hops haben deutlich weniger: der Router
      beantwortet Pings gedrosselt, leitet aber weiter (kein Problem)
    - ``loss`` – Verlust beginnt hier und bleibt bis zum Ziel (echter Verlust ab diesem Hop)
    - ``latency`` – Laufzeit springt hier um mehr als 30 ms und bleibt bis zum Ziel hoch
    - ``ok`` / ``no_data``
    """
    out = [dict(r) for r in rows]
    for r in out:
        r["loss_pct"] = round(r["loss"] * 100.0 / r["count"], 1) if r.get("count") else None
    for i, r in enumerate(out):
        later = [x for x in out[i + 1 :] if x["loss_pct"] is not None]
        prev = [x for x in out[:i] if x.get("rtt_avg") is not None]
        verdict = "ok" if r["loss_pct"] is not None else "no_data"
        if r["loss_pct"] is not None and r["loss_pct"] >= HOP_LOSS_PCT:
            if later and min(x["loss_pct"] for x in later) < r["loss_pct"] / 2:
                verdict = "icmp_limit"
            elif not any(x.get("verdict") == "loss" for x in out[:i]):
                verdict = "loss"
            else:
                verdict = "loss_continues"
        elif r.get("rtt_avg") is not None and prev:
            base = prev[-1]["rtt_avg"]
            later_rtts = [x["rtt_avg"] for x in out[i + 1 :] if x.get("rtt_avg") is not None]
            if r["rtt_avg"] - base > max(LATENCY_JUMP_MS, base * 0.5) and all(
                v >= r["rtt_avg"] - LATENCY_KEEP_MS for v in later_rtts
            ):
                verdict = "latency"
        r["verdict"] = verdict
        r["verdict_label"] = {
            "ok": "in Ordnung",
            "no_data": "keine Messwerte",
            "icmp_limit": "ICMP-Drosselung (kein echter Verlust)",
            "loss": "Verlust beginnt hier",
            "loss_continues": "Verlust (Folge eines früheren Hops)",
            "latency": "Laufzeit steigt hier deutlich",
        }[verdict]
    return out


def path_events(target: str, changed: bool, reached: bool, was_reached: bool | None) -> list[Event]:
    events = []
    if changed:
        events.append(Event("path_change", "info", f"Pfad zu {target} hat sich geändert"))
    if not reached and was_reached:
        events.append(Event("path_unreached", "warning", f"Ziel {target} beim Traceroute nicht erreicht"))
    return events


# --- Bandbreite -------------------------------------------------------------
def validate_bandwidth(data) -> tuple[str, list[dict]]:
    """Körper von ``POST /results/bandwidth`` → (trigger, Ergebnisse je Richtung)."""
    if not isinstance(data, dict):
        raise ResultError("JSON-Objekt erwartet")
    trigger = "manual" if data.get("trigger") == "manual" else "schedule"
    results = []
    raw = data.get("results")
    for r in (raw if isinstance(raw, list) else [])[:4]:
        if not isinstance(r, dict) or r.get("direction") not in ("up", "down"):
            continue
        results.append(
            {
                "direction": r["direction"],
                "peer": str(r.get("peer") or "")[:200] or None,
                "protocol": str(r.get("protocol") or "")[:10] or None,
                "mbps": num(r.get("mbps"), 0, 1_000_000),
                "jitter_ms": num(r.get("jitter_ms"), 0, 60000),
                "loss_pct": num(r.get("loss_pct"), 0, 100),
                "retransmits": _count(r.get("retransmits")),
                "latency_ms": num(r.get("latency_ms"), 0, 60000),
                "duration_s": num(r.get("duration_s"), 0, 3600),
                "error": str(r.get("error") or "")[:500] or None,
            }
        )
    if not results:
        raise ResultError("keine gültigen Ergebnisse")
    return trigger, results


def bandwidth_events(name: str, params: dict, results: list[dict]) -> list[Event]:
    events = []
    labels = {"down": "Download", "up": "Upload"}
    for r in results:
        label = labels[r["direction"]]
        if r["error"] or r["mbps"] is None:
            events.append(
                Event(
                    "bandwidth_error",
                    "warning",
                    f"{name}: {label} fehlgeschlagen",
                    r["error"] or "kein Messwert",
                )
            )
            continue
        limit = float(params.get(f"min_{r['direction']}_mbps") or 0)
        if limit and r["mbps"] < limit:
            events.append(
                Event(
                    "bandwidth_low",
                    "warning",
                    f"{name}: {label} {r['mbps']:.1f} Mbit/s unter {limit:g} Mbit/s",
                )
            )
    return events


# --- Sprache ----------------------------------------------------------------
def validate_rtp(data) -> tuple[str, dict]:
    """Körper von ``POST /results/rtp`` → (trigger, Werte)."""
    if not isinstance(data, dict):
        raise ResultError("JSON-Objekt erwartet")
    trigger = "manual" if data.get("trigger") == "manual" else "schedule"
    r = data.get("result")
    if not isinstance(r, dict):
        raise ResultError("result fehlt")
    return trigger, {
        "peer": str(r.get("peer") or "")[:200] or None,
        "codec": str(r.get("codec") or "")[:10] or None,
        "ptime_ms": _count(r.get("ptime_ms")),
        "duration_s": num(r.get("duration_s"), 0, 3600),
        "mode": r.get("mode") if r.get("mode") in ("wachtl", "echo") else None,
        "sent": _count(r.get("sent")),
        "received": _count(r.get("received")),
        "rtt_ms": num(r.get("rtt_ms"), 0, 60000),
        "rtt_max_ms": num(r.get("rtt_max_ms"), 0, 60000),
        "loss_pct": num(r.get("loss_pct"), 0, 100),
        "up_loss_pct": num(r.get("up_loss_pct"), 0, 100),
        "down_loss_pct": num(r.get("down_loss_pct"), 0, 100),
        "jitter_ms": num(r.get("jitter_ms"), 0, 60000),
        "up_jitter_ms": num(r.get("up_jitter_ms"), 0, 60000),
        "down_jitter_ms": num(r.get("down_jitter_ms"), 0, 60000),
        "out_of_order": _count(r.get("out_of_order")),
        "r_factor": num(r.get("r_factor"), -200, 200),
        "mos": num(r.get("mos"), 1, 5),
        "error": str(r.get("error") or "")[:500] or None,
    }


MOS_LEVELS = ((4.3, "sehr gut"), (4.0, "gut"), (3.6, "ausreichend"), (3.1, "mangelhaft"), (0.0, "schlecht"))


def mos_label(value: float | None) -> str:
    if value is None:
        return "–"
    return next(label for limit, label in MOS_LEVELS if value >= limit)


def rtp_events(name: str, params: dict, values: dict) -> list[Event]:
    if values["error"] or values["mos"] is None:
        return [
            Event("rtp_error", "warning", f"{name}: Sprachtest fehlgeschlagen", values["error"] or "kein MOS")
        ]
    limit = float(params.get("min_mos") or 0)
    if limit and values["mos"] < limit:
        sev = "critical" if values["mos"] < 3.1 else "warning"
        return [
            Event(
                "mos_low",
                sev,
                f"{name}: Sprachqualität {mos_label(values['mos'])} (MOS {values['mos']:.2f})",
                f"Schwellwert {limit:g}; Verlust {values['loss_pct'] or 0:.1f} %, "
                f"Jitter {values['jitter_ms'] or 0:.1f} ms, Laufzeit {values['rtt_ms'] or 0:.0f} ms",
            )
        ]
    return []
