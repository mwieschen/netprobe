"""Geo-/ASN-Auflösung für Traceroute-Hops (Hybrid: lokale MaxMind-DB → ip-api.com).

Reihenfolge je IP:

1. Cache (Tabelle ``geoip_cache``, TTL ``geoip_cache_days``).
2. Lokale MaxMind ``GeoLite2-City`` / ``GeoLite2-ASN`` ``.mmdb``, falls vorhanden.
3. ip-api.com Batch-Endpoint (HTTP), falls ``geoip_online_enabled``.

Private/Bogon-IPs (RFC1918, CGNAT 100.64/10, Loopback, Link-Local, Multicast,
reserviert) werden **nie** an einen externen Dienst gesendet. Jede Auflösung ist
best effort – Fehler führen zu Hops ohne Geo, nie zu einer Ausnahme.
"""

from __future__ import annotations

import ipaddress
import threading
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.models.traceroute import GeoipCache

log = get_logger("netprobe.geoip")

_CGNAT = ipaddress.ip_network("100.64.0.0/10")

# --- MaxMind Reader-Cache (Datei-mtime-basiert neu geöffnet) --------------------
_readers: dict[str, tuple[float, object]] = {}
_readers_lock = threading.Lock()


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _open_reader(path) -> object | None:
    if not path or not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _readers_lock:
        cached = _readers.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            import maxminddb

            reader = maxminddb.open_database(str(path))
        except Exception as exc:  # noqa: BLE001 - fehlende/kaputte DB ist nicht fatal
            log.warning("MaxMind-DB %s nicht nutzbar: %s", path, exc)
            return None
        _readers[key] = (mtime, reader)
        return reader


def _mmdb_lookup(ip: str) -> dict | None:
    if not settings.geoip_enabled:
        return None
    city_reader = _open_reader(settings.geoip_db_city)
    asn_reader = _open_reader(settings.geoip_db_asn)
    if city_reader is None and asn_reader is None:
        return None

    out: dict = {"source": "mmdb"}
    try:
        if city_reader is not None:
            rec = city_reader.get(ip) or {}  # type: ignore[attr-defined]
            out["country"] = (rec.get("country", {}).get("names", {}) or {}).get("en")
            out["city"] = (rec.get("city", {}).get("names", {}) or {}).get("en")
            loc = rec.get("location", {}) or {}
            out["lat"] = loc.get("latitude")
            out["lon"] = loc.get("longitude")
        if asn_reader is not None:
            rec = asn_reader.get(ip) or {}  # type: ignore[attr-defined]
            num = rec.get("autonomous_system_number")
            out["asn"] = f"AS{num}" if num else None
            out["isp"] = rec.get("autonomous_system_organization")
    except Exception as exc:  # noqa: BLE001
        log.warning("MaxMind-Lookup %s fehlgeschlagen: %s", ip, exc)
        return None

    if any(out.get(k) for k in ("country", "city", "asn", "isp")):
        return out
    return None


# --- ip-api.com (HTTP) --------------------------------------------------------
_rate_lock = threading.Lock()
_rate_calls: list[float] = []


def _rate_ok() -> bool:
    now = time.monotonic()
    with _rate_lock:
        _rate_calls[:] = [t for t in _rate_calls if now - t < 60]
        if len(_rate_calls) >= settings.geoip_online_rate_per_min:
            return False
        _rate_calls.append(now)
        return True


def _online_lookup(ips: list[str]) -> dict[str, dict]:
    if not (settings.geoip_enabled and settings.geoip_online_enabled) or not ips:
        return {}
    result: dict[str, dict] = {}
    fields = "status,message,country,city,lat,lon,as,isp,query"
    for i in range(0, len(ips), 100):
        chunk = ips[i : i + 100]
        if not _rate_ok():
            log.info("ip-api.com Rate-Limit erreicht – %d IPs ohne Online-Geo", len(ips) - i)
            break
        payload = [{"query": ip, "fields": fields} for ip in chunk]
        try:
            resp = httpx.post(settings.geoip_endpoint, json=payload, timeout=5.0)
            resp.raise_for_status()
            rows = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("ip-api.com nicht erreichbar: %s", exc)
            break
        for row in rows if isinstance(rows, list) else []:
            ip = row.get("query")
            if not ip or row.get("status") != "success":
                continue
            as_field = (row.get("as") or "").split(" ", 1)
            result[ip] = {
                "source": "ip-api",
                "country": row.get("country"),
                "city": row.get("city"),
                "lat": row.get("lat"),
                "lon": row.get("lon"),
                "asn": as_field[0] or None,
                "isp": row.get("isp") or (as_field[1] if len(as_field) > 1 else None),
            }
    return result


# --- öffentliche API --------------------------------------------------------
_GEO_KEYS = ("country", "city", "lat", "lon", "asn", "isp")


def lookup(db: Session, ips: list[str]) -> dict[str, dict]:
    """Löst eine Liste von IPs auf. Rückgabe ``{ip: {country, city, lat, lon, asn, isp}}``
    (nur für auflösbare, öffentliche IPs)."""
    if not settings.geoip_enabled:
        return {}
    wanted = sorted({ip for ip in ips if ip and _is_public(ip)})
    if not wanted:
        return {}

    out: dict[str, dict] = {}
    ttl = utcnow().timestamp() - settings.geoip_cache_days * 86400

    cached = db.execute(select(GeoipCache).where(GeoipCache.ip.in_(wanted))).scalars().all()
    fresh_ips = set()
    for row in cached:
        if row.fetched_at.timestamp() >= ttl:
            out[row.ip] = {k: getattr(row, k) for k in _GEO_KEYS}
            fresh_ips.add(row.ip)

    missing = [ip for ip in wanted if ip not in fresh_ips]

    # 2) lokale MaxMind-DB
    online_needed: list[str] = []
    new_rows: dict[str, dict] = {}
    for ip in missing:
        info = _mmdb_lookup(ip)
        if info:
            new_rows[ip] = info
        else:
            online_needed.append(ip)

    # 3) ip-api.com
    if online_needed:
        new_rows.update(_online_lookup(online_needed))

    # Cache aktualisieren
    now = utcnow()
    for ip, info in new_rows.items():
        existing = db.get(GeoipCache, ip)
        values = {k: info.get(k) for k in _GEO_KEYS}
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            existing.source = info.get("source", "")
            existing.fetched_at = now
        else:
            db.add(GeoipCache(ip=ip, source=info.get("source", ""), fetched_at=now, **values))
        out[ip] = values
    if new_rows:
        db.commit()

    return out
