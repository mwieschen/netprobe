"""Reine Funktionen für die Wachtl-Probe-API: Ingest-Parser, Verdichtung, SPKI-Pin, Zustand.

Ohne DB/IO, damit sie direkt testbar sind (wie ``tshark_parsers``). Das Ingest-Format entspricht
Wachtl 0.9 (``server/wachtl/services/ingest.py``)::

    wachtl_ping_rtt_ms{check_id="3"} 12.41 1760620800000

Die Probe darf nur ``check_id`` senden; alles andere ergibt sich aus der DB. Rohwerte werden
nicht gespeichert, sondern direkt zu 1-Minuten-Buckets je Check verdichtet.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
import zlib
from dataclasses import dataclass, field

PING_UP = "wachtl_ping_up"
PING_RTT = "wachtl_ping_rtt_ms"
PING_TTL = "wachtl_ping_ttl"
PING_JITTER = "wachtl_ping_jitter_ms"
ALLOWED_METRICS = frozenset({PING_UP, PING_RTT, PING_TTL, PING_JITTER})

MAX_AGE_MS = 8 * 86400 * 1000      # Puffer der Probe hält 7 Tage
MAX_FUTURE_MS = 5 * 60 * 1000      # Uhrabweichung
MAX_RTT_MS = 60_000.0
BUCKET_S = 60
MIN_WARN_LOSS = 2

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PROBE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)\{(.*)\}\s+(\S+)\s+(\d{10,16})$")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


class BodyTooLarge(ValueError):
    pass


@dataclass
class Bucket:
    """Akkumulator eines 1-Minuten-Buckets (Felder wie ``WachtlSample``)."""

    count: int = 0
    loss: int = 0
    rtt_min: float | None = None
    rtt_max: float | None = None
    rtt_sum: float = 0.0
    rtt_n: int = 0
    jitter_sum: float = 0.0
    jitter_n: int = 0
    ttl: int | None = None

    def add_rtt(self, v: float) -> None:
        self.rtt_sum += v
        self.rtt_n += 1
        self.rtt_min = v if self.rtt_min is None else min(self.rtt_min, v)
        self.rtt_max = v if self.rtt_max is None else max(self.rtt_max, v)


@dataclass
class IngestResult:
    # Schlüssel: (check_id, Bucket-Beginn in Unix-Sekunden)
    buckets: dict[tuple[int, int], Bucket] = field(default_factory=dict)
    accepted: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def rejected(self) -> int:
        return sum(self.reasons.values())


def decompress_gzip(raw: bytes, max_bytes: int) -> bytes:
    """gzip entpacken, höchstens ``max_bytes`` (Schutz vor gzip-Bomben)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(raw, max_bytes)
    if d.unconsumed_tail:
        raise BodyTooLarge("Batch zu groß")
    out += d.flush()
    if len(out) > max_bytes:
        raise BodyTooLarge("Batch zu groß")
    return out


def parse_ingest(body: str, check_ids: set[int], now_ms: int) -> IngestResult:
    """Zeilen prüfen und zu Buckets verdichten. Unbekannte Checks/Metriken werden gezählt verworfen."""
    res = IngestResult()

    def reject(reason: str) -> None:
        res.reasons[reason] = res.reasons.get(reason, 0) + 1

    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            reject("format")
            continue
        name, label_str, value_str, ts_str = m.groups()
        if name not in ALLOWED_METRICS:
            reject("metric")
            continue
        labels = dict(_LABEL_RE.findall(label_str))
        cid_str = labels.get("check_id", "")
        if not (cid_str.isascii() and cid_str.isdigit()) or int(cid_str) not in check_ids:
            reject("check_id")
            continue
        try:
            value = float(value_str)
        except ValueError:
            reject("value")
            continue
        if math.isnan(value) or math.isinf(value):
            reject("value")
            continue
        ts = int(ts_str)
        if ts < 10**12:  # Sekunden statt Millisekunden
            ts *= 1000
        if ts < now_ms - MAX_AGE_MS or ts > now_ms + MAX_FUTURE_MS:
            reject("timestamp")
            continue

        key = (int(cid_str), (ts // 1000) // BUCKET_S * BUCKET_S)
        b = res.buckets.get(key)
        if b is None:
            b = res.buckets[key] = Bucket()
        if name == PING_UP:
            b.count += 1
            if value < 0.5:
                b.loss += 1
        elif name == PING_RTT:
            if not 0 <= value <= MAX_RTT_MS:
                reject("value")
                continue
            b.add_rtt(value)
        elif name == PING_JITTER:
            if not 0 <= value <= MAX_RTT_MS:
                reject("value")
                continue
            b.jitter_sum += value
            b.jitter_n += 1
        elif name == PING_TTL:
            if not 0 <= value <= 255:
                reject("value")
                continue
            b.ttl = int(value)
        res.accepted += 1
    return res


def merge_bucket(row, b: Bucket) -> None:
    """Bucket in eine bestehende Zeile (``WachtlSample`` oder gleichartiges Objekt) einrechnen."""
    row.count += b.count
    row.loss += b.loss
    row.rtt_sum += b.rtt_sum
    row.rtt_n += b.rtt_n
    row.jitter_sum += b.jitter_sum
    row.jitter_n += b.jitter_n
    if b.rtt_min is not None:
        row.rtt_min = b.rtt_min if row.rtt_min is None else min(row.rtt_min, b.rtt_min)
    if b.rtt_max is not None:
        row.rtt_max = b.rtt_max if row.rtt_max is None else max(row.rtt_max, b.rtt_max)
    if b.ttl is not None:
        row.ttl = b.ttl


# --- SPKI-Pin ---------------------------------------------------------------
def _der_read(buf: bytes, pos: int) -> tuple[int, int, int]:
    """DER-TLV ab ``pos`` lesen → (tag, Inhaltsbeginn, Inhaltsende)."""
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4:
            raise ValueError("DER-Länge ungültig")
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    end = pos + length
    if end > len(buf):
        raise ValueError("DER abgeschnitten")
    return tag, pos, end


def spki_pin_from_pem(pem: str) -> str:
    """SHA-256 über SubjectPublicKeyInfo des ersten Zertifikats, Base64 – wie ``SpkiPin.Compute`` der Probe.

    Entspricht ``openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256
    -binary | base64``.
    """
    m = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S)
    if not m:
        raise ValueError("Kein Zertifikat im PEM gefunden")
    der = base64.b64decode("".join(m.group(1).split()))
    tag, pos, _ = _der_read(der, 0)                 # Certificate
    if tag != 0x30:
        raise ValueError("Kein X.509-Zertifikat")
    tag, pos, tbs_end = _der_read(der, pos)         # tbsCertificate
    if tag != 0x30:
        raise ValueError("Kein X.509-Zertifikat")
    # version [0] (optional), serialNumber, signature, issuer, validity, subject, subjectPublicKeyInfo
    fields = 0
    while pos < tbs_end:
        start = pos
        tag, _, end = _der_read(der, pos)
        pos = end
        if tag == 0xA0:
            continue
        fields += 1
        if fields == 6:
            if tag != 0x30:
                raise ValueError("SubjectPublicKeyInfo nicht gefunden")
            return base64.b64encode(hashlib.sha256(der[start:end]).digest()).decode()
    raise ValueError("SubjectPublicKeyInfo nicht gefunden")


# --- Zustand ----------------------------------------------------------------
def classify_check(count: int, loss: int, rtt_avg: float | None, *, loss_warn_pct: float,
                   latency_warn_ms: float) -> str:
    """Zustand eines Checks aus den Werten der letzten Minuten: ok / warn / down / stale.

    Verlust zählt erst ab ``MIN_WARN_LOSS`` verlorenen Pings: bei 5-s-Intervall sind es nur 60 Pings
    in 5 Minuten, ein einzelner Ausreißer (1,7 %) würde sonst schon als „auffällig“ gelten.
    """
    if count <= 0:
        return "stale"
    loss_pct = loss * 100.0 / count
    if loss >= count:
        return "down"
    if (loss >= MIN_WARN_LOSS and loss_pct >= loss_warn_pct) or (
            rtt_avg is not None and rtt_avg > latency_warn_ms):
        return "warn"
    return "ok"
