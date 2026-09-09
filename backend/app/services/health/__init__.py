"""Framework für System-Health-Checks (DNS, HTTP(S), SMTP, IMAP, NTP).

Jeder Prüftyp stellt eine Funktion ``run(target: str, params: dict) -> HealthOutcome``
bereit. Alle Prüfungen haben ein hartes Timeout und geben ein strukturiertes
Ergebnis zurück statt eine Ausnahme zu werfen.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.health_check import HealthCheckResult, HealthProbe

log = get_logger("netprobe.health")


@dataclass
class HealthOutcome:
    ok: bool
    response_ms: float | None = None
    status: str | None = None
    details: dict = field(default_factory=dict)
    error: str | None = None


# Wird von den Submodulen beim Import befüllt.
_REGISTRY: dict[str, Callable[[str, dict], HealthOutcome]] = {}


def register(check_type: str, fn: Callable[[str, dict], HealthOutcome]) -> None:
    _REGISTRY[check_type] = fn


def available_types() -> list[str]:
    return sorted(_REGISTRY)


def run_check(check_type: str, target: str, params: dict) -> HealthOutcome:
    fn = _REGISTRY.get(check_type)
    if fn is None:
        return HealthOutcome(ok=False, error=f"Unbekannter Prüftyp: {check_type!r}")
    try:
        return fn(target, params or {})
    except Exception as exc:  # noqa: BLE001 - Prüfung darf nie den Aufrufer sprengen
        log.warning("Health-Check %s/%s fehlgeschlagen: %s", check_type, target, exc)
        return HealthOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")


def run_probe(db: Session, probe: HealthProbe, *, source: str = "manual") -> HealthCheckResult:
    try:
        params = json.loads(probe.params_json or "{}")
    except (json.JSONDecodeError, TypeError):
        params = {}

    outcome = run_check(probe.check_type, probe.target, params)
    result = HealthCheckResult(
        probe_id=probe.id,
        source=source,
        ok=outcome.ok,
        response_ms=outcome.response_ms,
        status=outcome.status,
        details_json=json.dumps(outcome.details, default=str),
        error=outcome.error,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    log.info("Health-Check %s (%s) ok=%s", probe.name, probe.check_type, outcome.ok)
    return result


# Submodule registrieren sich beim Import.
from app.services.health import dns, http, ntp, smtp_imap  # noqa: E402,F401
