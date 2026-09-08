"""Speedtest-Ausführung über die offizielle Ookla Speedtest CLI."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.speedtest import SpeedtestResult
from app.services.parsers import parse_speedtest_json
from app.services.proc import ToolNotFoundError, run_sync

log = get_logger("netprobe.speedtest")

SPEEDTEST_LOCK = "speedtest_lock"


def build_args(server_id: int | None) -> list[str]:
    args = [
        settings.speedtest_bin,
        "--format=json",
        "--accept-license",
        "--accept-gdpr",
        "--progress=no",
    ]
    if server_id is not None:
        args += ["--server-id", str(server_id)]
    return args


def run_speedtest(db: Session, *, source: str = "manual", server_id: int | None = None) -> SpeedtestResult:
    """Führt einen Speedtest aus und persistiert das Ergebnis.

    Der Aufrufer ist dafür verantwortlich, dass der Job-Lock gehalten wird
    (siehe :func:`app.services.kvstore.acquire_lock`)."""
    if server_id is None:
        server_id = settings.speedtest_server_id

    try:
        proc = run_sync(build_args(server_id), timeout=180)
    except ToolNotFoundError as exc:
        result = SpeedtestResult(ok=False, source=source, error=str(exc))
        db.add(result)
        db.commit()
        db.refresh(result)
        return result

    if proc.returncode != 0 and not proc.stdout.strip():
        parsed_error = proc.stderr.strip() or f"Exit-Code {proc.returncode}"
        result = SpeedtestResult(ok=False, source=source, error=parsed_error)
        db.add(result)
        db.commit()
        db.refresh(result)
        return result

    parsed = parse_speedtest_json(proc.stdout)
    result = SpeedtestResult(
        ok=parsed.ok,
        download_mbps=parsed.download_mbps,
        upload_mbps=parsed.upload_mbps,
        ping_ms=parsed.ping_ms,
        jitter_ms=parsed.jitter_ms,
        packet_loss=parsed.packet_loss,
        server_id=parsed.server_id,
        server_name=parsed.server_name,
        source=source,
        error=parsed.error,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    log.info("Speedtest ok=%s down=%s up=%s", parsed.ok, parsed.download_mbps, parsed.upload_mbps)
    return result


def list_servers() -> list[dict]:
    """Listet verfügbare Speedtest-Server (``speedtest --servers``)."""
    import json

    try:
        proc = run_sync(
            [settings.speedtest_bin, "--servers", "--format=json", "--accept-license", "--accept-gdpr"],
            timeout=30,
        )
    except ToolNotFoundError:
        return []
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    return data.get("servers", [])
