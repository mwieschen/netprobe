"""Check-Typen der Wachtl-Clients und ihre Einstellungen (rein, ohne DB).

Jede Einstellung ist hier genau einmal beschrieben – wie in Wachtl ``server/wachtl/checktypes.py``, dessen
Parameternamen und Standardwerte die Probe erwartet. Daraus entstehen das Formular im UI
(``GET /api/clients/check-types``), die Prüfung der Eingaben und die Werte in ``/config``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import urlparse

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
URL_RE = re.compile(r"^https?://[^\s\"'<>]{3,500}$")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # int | float | bool | choice | text | time | url | host
    default: object
    min: float | None = None
    max: float | None = None
    choices: tuple[tuple[str, str], ...] = ()
    help: str = ""
    unit: str = ""
    show_if: tuple[str, tuple[str, ...]] | None = None  # (Feld, erlaubte Werte)
    group: str = "Messung"


@dataclass(frozen=True)
class CheckType:
    key: str
    label: str
    description: str
    uses_target: bool  # Ziel als eigenes Feld (ping/path); sonst ergibt es sich aus den Parametern
    uses_interval: bool  # Dauer-Messung im Sekundentakt (ping/path)
    fields: tuple[Field, ...] = field(default_factory=tuple)

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.fields}


DIRECTIONS = (
    ("down", "Download (Gegenstelle → Probe)"),
    ("up", "Upload (Probe → Gegenstelle)"),
    ("both", "Beide Richtungen nacheinander"),
)
SCHEDULE_FIELDS = (
    Field(
        "schedule_min",
        "Automatisch messen alle",
        "int",
        60,
        0,
        10080,
        unit="min",
        help="0 = nur auf Knopfdruck „Jetzt messen“.",
        group="Zeitplan",
    ),
    Field(
        "offset_min",
        "Versatz",
        "int",
        -1,
        -1,
        10079,
        unit="min",
        help="-1 = automatisch je Probe verteilt, damit nicht alle Probes gleichzeitig messen.",
        group="Zeitplan",
    ),
    Field("window_start", "Nur zwischen", "time", "00:00", help="Ortszeit der Probe", group="Zeitplan"),
    Field("window_end", "und", "time", "23:59", group="Zeitplan"),
    Field(
        "weekdays",
        "An Wochentagen",
        "text",
        "1234567",
        help="Ziffern 1 = Montag … 7 = Sonntag, z. B. 12345 für Werktage.",
        group="Zeitplan",
    ),
)
THRESHOLD_FIELDS = (
    Field(
        "min_down_mbps",
        "Ereignis, wenn Download unter",
        "float",
        0,
        0,
        100000,
        unit="Mbit/s",
        help="0 = keine Prüfung",
        group="Schwellwerte",
    ),
    Field(
        "min_up_mbps",
        "Ereignis, wenn Upload unter",
        "float",
        0,
        0,
        100000,
        unit="Mbit/s",
        help="0 = keine Prüfung",
        group="Schwellwerte",
    ),
)
# Sprachtest: kurze Tests häufiger als Bandbreitentests (ein Gespräch belastet die Leitung kaum)
RTP_SCHEDULE_FIELDS = tuple(replace(f, default=15) if f.key == "schedule_min" else f for f in SCHEDULE_FIELDS)
PEER_CHOICES = (("server", "netprobe"), ("probe", "andere Probe"), ("host", "eigener Server"))

TYPES: dict[str, CheckType] = {
    "ping": CheckType(
        "ping",
        "Ping (Dauerping)",
        "Erreichbarkeit, Latenz, Paketverlust und Jitter zu einem Ziel.",
        True,
        True,
        (
            Field(
                "size_bytes", "Paketgröße", "int", 32, 0, 1472, unit="Byte", help="Nutzdaten je ICMP-Paket"
            ),
            Field("ttl", "TTL", "int", 128, 1, 255),
            Field("jitter_window_s", "Jitter berechnen über", "int", 60, 10, 3600, unit="s"),
        ),
    ),
    "path": CheckType(
        "path",
        "Pfad (Traceroute + Hop-Pings)",
        "Ermittelt alle Router bis zum Ziel und pingt jeden Hop dauerhaft. So sieht man, ab welchem Hop "
        "Verluste oder Verzögerungen beginnen.",
        True,
        True,
        (
            Field("max_hops", "Maximale Hops", "int", 20, 1, 64),
            Field("hop_size_bytes", "Paketgröße", "int", 32, 0, 1472, unit="Byte"),
            Field("trace_interval_min", "Pfad neu ermitteln alle", "int", 10, 1, 1440, unit="min"),
            Field("retrace_on_loss", "Pfad sofort neu ermitteln bei Verlust am Ziel", "bool", True),
            Field("trace_timeout_ms", "Timeout je Traceroute-Hop", "int", 1000, 100, 5000, unit="ms"),
            Field("probes_per_hop", "Versuche je Hop beim Traceroute", "int", 2, 1, 5),
            Field(
                "ping_hops",
                "Hops",
                "choice",
                "all",
                choices=(
                    ("all", "alle Hops dauerhaft pingen"),
                    ("first", "nur die ersten N Hops"),
                    ("none", "nur Traceroute, keine Dauerpings"),
                ),
            ),
            Field("first_n", "N", "int", 5, 1, 64, show_if=("ping_hops", ("first",))),
            Field(
                "skip_private",
                "Private Hops (LAN) überspringen",
                "bool",
                False,
                help="z. B. wenn das LAN bereits mit eigenen Checks überwacht wird.",
            ),
        ),
    ),
    "iperf": CheckType(
        "iperf",
        "Bandbreite intern (iperf3)",
        "Durchsatz zwischen PC und netprobe, zwischen zwei PCs oder zu einem eigenen iperf3-Server.",
        False,
        False,
        (
            Field("peer", "Gegenstelle", "choice", "server", choices=PEER_CHOICES, group="Gegenstelle"),
            Field(
                "peer_probe_id", "Probe", "int", 0, 0, None, show_if=("peer", ("probe",)), group="Gegenstelle"
            ),
            Field("host", "Host", "host", "", show_if=("peer", ("host",)), group="Gegenstelle"),
            Field(
                "port",
                "Port",
                "int",
                5201,
                1,
                65535,
                show_if=("peer", ("host",)),
                group="Gegenstelle",
                help="netprobe und Probes nutzen ihren eingestellten iperf3-Port.",
            ),
            Field("protocol", "Protokoll", "choice", "tcp", choices=(("tcp", "TCP"), ("udp", "UDP"))),
            Field("direction", "Richtung", "choice", "both", choices=DIRECTIONS),
            Field("duration_s", "Testdauer", "int", 10, 1, 120, unit="s"),
            Field(
                "omit_s",
                "Anlaufzeit ignorieren",
                "int",
                1,
                0,
                30,
                unit="s",
                help="TCP-Slow-Start nicht mitmessen",
            ),
            Field("streams", "Parallele Streams", "int", 1, 1, 32),
            Field(
                "udp_mbps",
                "UDP-Sendeleistung",
                "float",
                100,
                0.1,
                10000,
                unit="Mbit/s",
                show_if=("protocol", ("udp",)),
            ),
            Field(
                "window_kb",
                "TCP-Fenster",
                "int",
                0,
                0,
                65536,
                unit="KB",
                help="0 = Betriebssystem-Standard",
                show_if=("protocol", ("tcp",)),
            ),
            Field(
                "timeout_s", "Abbruch nach", "int", 60, 10, 600, unit="s", help="falls die Gegenstelle hängt"
            ),
        )
        + SCHEDULE_FIELDS
        + THRESHOLD_FIELDS,
    ),
    "speedtest": CheckType(
        "speedtest",
        "Bandbreite WAN (Internet)",
        "Durchsatz ins Internet – per HTTP-Download/-Upload oder zu einem iperf3-Server im Internet.",
        False,
        False,
        (
            Field(
                "method",
                "Verfahren",
                "choice",
                "http",
                choices=(("http", "HTTP (Download/Upload von URLs)"), ("iperf", "iperf3-Server im Internet")),
                group="Gegenstelle",
            ),
            Field(
                "down_url",
                "Download-URL",
                "url",
                "https://speed.cloudflare.com/__down?bytes=25000000",
                show_if=("method", ("http",)),
                group="Gegenstelle",
                help="Wird wiederholt geladen, bis die Testdauer erreicht ist.",
            ),
            Field(
                "up_url",
                "Upload-URL",
                "url",
                "https://speed.cloudflare.com/__up",
                show_if=("method", ("http",)),
                group="Gegenstelle",
                help="Leer = kein Upload-Test",
            ),
            Field(
                "latency_url",
                "Latenz-URL",
                "url",
                "https://speed.cloudflare.com/__down?bytes=0",
                show_if=("method", ("http",)),
                group="Gegenstelle",
                help="Leer = keine Latenzmessung",
            ),
            Field(
                "use_proxy",
                "Proxy der Probe verwenden",
                "bool",
                False,
                show_if=("method", ("http",)),
                group="Gegenstelle",
            ),
            Field("host", "iperf3-Host", "host", "", show_if=("method", ("iperf",)), group="Gegenstelle"),
            Field("port", "Port", "int", 5201, 1, 65535, show_if=("method", ("iperf",)), group="Gegenstelle"),
            Field(
                "protocol",
                "Protokoll",
                "choice",
                "tcp",
                choices=(("tcp", "TCP"), ("udp", "UDP")),
                show_if=("method", ("iperf",)),
            ),
            Field(
                "udp_mbps",
                "UDP-Sendeleistung",
                "float",
                100,
                0.1,
                10000,
                unit="Mbit/s",
                show_if=("protocol", ("udp",)),
            ),
            Field("direction", "Richtung", "choice", "both", choices=DIRECTIONS),
            Field("duration_s", "Testdauer je Richtung", "int", 10, 2, 120, unit="s"),
            Field("streams", "Parallele Verbindungen", "int", 4, 1, 32),
            Field("timeout_s", "Abbruch nach", "int", 90, 10, 600, unit="s"),
        )
        + SCHEDULE_FIELDS
        + THRESHOLD_FIELDS,
    ),
    "rtp": CheckType(
        "rtp",
        "Sprache (RTP/VoIP)",
        "Simuliert ein Telefonat: der PC sendet einen RTP-Sprachstrom an einen Reflektor und misst je "
        "Richtung "
        "Laufzeit, Jitter und Paketverlust. Daraus ergibt sich die Sprachqualität als MOS (1–4,5).",
        False,
        False,
        (
            Field("peer", "Gegenstelle", "choice", "server", choices=PEER_CHOICES, group="Gegenstelle"),
            Field(
                "peer_probe_id", "Probe", "int", 0, 0, None, show_if=("peer", ("probe",)), group="Gegenstelle"
            ),
            Field(
                "host",
                "Host",
                "host",
                "",
                show_if=("peer", ("host",)),
                group="Gegenstelle",
                help="Ein Wachtl-Reflektor oder ein UDP-Echo-Dienst (dann nur Hin- und Rückweg zusammen).",
            ),
            Field(
                "port", "UDP-Port", "int", 5004, 1, 65535, show_if=("peer", ("host",)), group="Gegenstelle"
            ),
            Field(
                "codec",
                "Codec",
                "choice",
                "g711",
                choices=(
                    ("g711", "G.711 (64 kbit/s, Standard bei SIP-Telefonen)"),
                    ("g729", "G.729 (8 kbit/s)"),
                ),
            ),
            Field(
                "ptime_ms",
                "Paketabstand",
                "int",
                20,
                10,
                60,
                unit="ms",
                help="Sprache je Paket; Telefone senden meist alle 20 ms, manche alle 30 ms.",
            ),
            Field("duration_s", "Testdauer", "int", 30, 5, 300, unit="s"),
            Field(
                "dscp",
                "DSCP-Markierung",
                "int",
                46,
                0,
                63,
                help="46 = EF (Sprache). Windows setzt sie nur mit QoS-Richtlinie (GPO) "
                "für wachtl-probe.exe.",
            ),
        )
        + RTP_SCHEDULE_FIELDS
        + (
            Field(
                "min_mos",
                "Ereignis, wenn MOS unter",
                "float",
                3.6,
                0,
                4.5,
                group="Schwellwerte",
                help="3,6 = viele Gesprächspartner merken Störungen. 0 = keine Prüfung.",
            ),
        ),
    ),
}

BANDWIDTH_TYPES = ("iperf", "speedtest")
SCHEDULED_TYPES = BANDWIDTH_TYPES + ("rtp",)
PEER_TYPES = ("iperf", "rtp")
# Hop-Pings (Pfad) und Dauerpings: Untergrenze für das Intervall gilt; geplante Messungen haben keins.
SCHEDULED_INTERVAL_S = 60


class ParamError(ValueError):
    def __init__(self, message: str, field_key: str | None = None) -> None:
        super().__init__(message)
        self.field = field_key


def valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOSTNAME_RE.match(value)) and not value.startswith("-")


def _visible(f: Field, values: dict) -> bool:
    return not f.show_if or str(values.get(f.show_if[0])) in f.show_if[1]


def _convert(f: Field, raw):
    if f.kind == "bool":
        return raw in (True, "on", "true", "1", 1)
    raw = "" if raw is None else str(raw).strip()
    if f.kind in ("int", "float"):
        if raw == "":
            return f.default
        try:
            v: float = int(raw) if f.kind == "int" else float(raw.replace(",", "."))
        except ValueError:
            raise ValueError("keine Zahl") from None
        if not math.isfinite(v):
            raise ValueError("keine Zahl")
        if f.min is not None and v < f.min:
            raise ValueError(f"mindestens {f.min:g}")
        if f.max is not None and v > f.max:
            raise ValueError(f"höchstens {f.max:g}")
        return v
    if f.kind == "choice":
        if raw not in [k for k, _ in f.choices]:
            raise ValueError("ungültige Auswahl")
        return raw
    if f.kind == "time":
        if not TIME_RE.match(raw):
            raise ValueError("Format HH:MM")
        return raw
    if f.kind == "url":
        if raw and not URL_RE.match(raw):
            raise ValueError("keine gültige http(s)-URL")
        return raw
    if f.kind == "host":
        if raw and not valid_host(raw):
            raise ValueError("keine gültige Adresse")
        return raw
    if len(raw) > 200:
        raise ValueError("zu lang")
    return raw


def parse_params(type_key: str, raw: dict | None, *, probe_ids: set[int] | None = None) -> dict:
    """Eingaben prüfen → vollständige Parameter; unsichtbare Felder behalten ihren Standard.
    Wirft ``ParamError`` mit dem ersten Fehler (Feldbezug für das Formular)."""
    if type_key not in TYPES:
        raise ParamError("Unbekannter Check-Typ", "type")
    raw = raw or {}
    values: dict = {}
    # zuerst Auswahlfelder, damit show_if ausgewertet werden kann
    for f in sorted(TYPES[type_key].fields, key=lambda f: 0 if f.kind == "choice" else 1):
        # Unsichtbar oder gar nicht gesendet → Standard; leer gesendet bleibt leer (z. B. keine Upload-URL).
        if (f.kind != "bool" and not _visible(f, values)) or f.key not in raw:
            values[f.key] = f.default
            continue
        try:
            values[f.key] = _convert(f, raw.get(f.key))
        except ValueError as exc:
            raise ParamError(f"{f.label}: {exc}", f.key) from None

    if type_key in PEER_TYPES:
        if values["peer"] == "host" and not values["host"]:
            raise ParamError("Host der Gegenstelle fehlt.", "host")
        if values["peer"] == "probe" and probe_ids is not None and values["peer_probe_id"] not in probe_ids:
            raise ParamError("Bitte eine Probe als Gegenstelle auswählen.", "peer_probe_id")
    if type_key == "speedtest":
        if values["method"] == "iperf" and not values["host"]:
            raise ParamError("iperf3-Host fehlt.", "host")
        if values["method"] == "http" and not values["down_url"] and not values["up_url"]:
            raise ParamError("Mindestens eine Download- oder Upload-URL angeben.", "down_url")
    if type_key in SCHEDULED_TYPES:
        if not values["weekdays"] or set(values["weekdays"]) - set("1234567"):
            raise ParamError("Wochentage: nur Ziffern 1 bis 7.", "weekdays")
        values["weekdays"] = "".join(sorted(set(values["weekdays"])))
        if values["schedule_min"] and values["offset_min"] >= values["schedule_min"]:
            raise ParamError("Versatz muss kleiner als das Messintervall sein.", "offset_min")
        if (
            "timeout_s" in values
            and values["duration_s"] * (2 if values["direction"] == "both" else 1) + 10 > values["timeout_s"]
        ):
            raise ParamError("„Abbruch nach“ ist kürzer als die Testdauer.", "timeout_s")
    return values


def merged(type_key: str, params: dict | None) -> dict:
    """Gespeicherte Parameter mit Standardwerten ergänzen (ältere Checks ohne neue Felder)."""
    out = TYPES[type_key].defaults() if type_key in TYPES else {}
    out.update({k: v for k, v in (params or {}).items() if type_key not in TYPES or k in out})
    return out


def timeout_ms(type_key: str, params: dict, default: int = 1000) -> int:
    """Spalte ``timeout_ms`` geplanter Checks: Abbruchzeit, beim Sprachtest Testdauer plus Reserve."""
    if type_key == "rtp":
        return (int(params["duration_s"]) + 10) * 1000
    if type_key in BANDWIDTH_TYPES:
        return int(params["timeout_s"]) * 1000
    return default


def target_for(type_key: str, params: dict, probe_names: dict[int, str] | None = None) -> str:
    """Anzeige-Ziel für Checks ohne eigenes Zielfeld (iperf/speedtest/rtp)."""
    if type_key in PEER_TYPES:
        if params["peer"] == "server":
            return "netprobe"
        if params["peer"] == "probe":
            name = (probe_names or {}).get(params["peer_probe_id"], str(params["peer_probe_id"]))
            return f"probe:{name}"
        return f"{params['host']}:{params['port']}"
    if type_key == "speedtest":
        if params["method"] == "iperf":
            return f"{params['host']}:{params['port']}"
        return urlparse(params["down_url"] or params["up_url"]).hostname or "internet"
    raise ValueError(type_key)


def schedule_offset(probe_id: int, check_id: int, schedule_min: int, offset_min: int) -> int:
    """Fester Versatz je Probe und Check, damit nicht alle Probes zur selben Minute messen."""
    if not schedule_min:
        return 0
    if offset_min >= 0:
        return offset_min % schedule_min
    digest = hashlib.sha256(f"{probe_id}:{check_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % schedule_min


def summary(type_key: str, interval_s: int, params: dict) -> str:
    """Kurzbeschreibung für Tabellen."""
    p = merged(type_key, params)
    if type_key == "ping":
        return f"alle {interval_s} s"
    if type_key == "path":
        hops = {"all": "alle Hops", "first": f"erste {p['first_n']} Hops", "none": "nur Traceroute"}[
            p["ping_hops"]
        ]
        return f"{hops} alle {interval_s} s, Pfad alle {p['trace_interval_min']} min"
    when = f"alle {p['schedule_min']} min" if p["schedule_min"] else "nur manuell"
    if type_key == "rtp":
        return f"{p['codec'].upper().replace('G', 'G.', 1)}, {p['ptime_ms']} ms, {p['duration_s']} s, {when}"
    direction = {"down": "↓", "up": "↑", "both": "↓↑"}[p["direction"]]
    proto = p.get("protocol", "tcp").upper() if type_key == "iperf" or p.get("method") == "iperf" else "HTTP"
    return f"{proto} {direction} {p['duration_s']} s, {p['streams']}×, {when}"


def describe() -> list[dict]:
    """Typen mit Feldern für das Formular im UI."""
    return [
        {
            "key": t.key,
            "label": t.label,
            "description": t.description,
            "uses_target": t.uses_target,
            "uses_interval": t.uses_interval,
            "fields": [
                {
                    **asdict(f),
                    "choices": [list(c) for c in f.choices],
                    "show_if": list(f.show_if) if f.show_if else None,
                }
                for f in t.fields
            ],
        }
        for t in TYPES.values()
    ]
