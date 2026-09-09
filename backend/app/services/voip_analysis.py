"""Analyse einer PCAP auf SIP-Dialoge, SDP-/Codec-Angebote und RTP-Streams.

Zwei tshark-Durchläufe:
- SIP: ``-Y sip -T fields`` (Feldreihenfolge = :data:`SIP_FIELDS`) → :func:`sip_parsers.parse_sip_messages`
- RTP: ``-q -z rtp,streams`` (mit RTP-Heuristik) → :func:`sip_parsers.parse_rtp_streams`

Läuft sowohl auf dem im VoIP-Test erzeugten Mitschnitt als auch – über
``POST /api/voip/analyze`` bzw. die CLI – auf einer beliebigen vorhandenen Capture.
Jeder Pfad wird gegen ``settings.capture_dir`` geprüft. Fehlende Tools oder leere
PCAPs führen zu einem leeren Ergebnis, nie zu einer Ausnahme.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.capture import Capture
from app.services import sip_parsers
from app.services.proc import ToolNotFoundError, run_sync

log = get_logger("netprobe.voip.analysis")

# Reihenfolge MUSS zu sip_parsers._N_FIELDS / parse_sip_messages passen.
SIP_FIELDS = (
    "frame.time_epoch",
    "ip.src",
    "ip.dst",
    "sip.Method",
    "sip.Status-Code",
    "sip.Status-Line",
    "sip.Call-ID",
    "sip.CSeq",
    "sip.Server",
    "sip.User-Agent",
    "sip.Allow",
    "sip.resend",
    "sdp.media",
    "sdp.media_attr",
)

_EMPTY: dict = {"sip": {}, "codecs": {}, "rtp": {}}


class VoipAnalysisError(ValueError):
    pass


def validate_capture_path(path: Path) -> Path:
    """Stellt sicher, dass ``path`` innerhalb von ``settings.capture_dir`` liegt."""
    try:
        path.resolve().relative_to(settings.capture_dir.resolve())
    except ValueError as exc:
        raise VoipAnalysisError("Pfad liegt außerhalb des Capture-Verzeichnisses.") from exc
    if not path.exists():
        raise VoipAnalysisError("Capture-Datei nicht gefunden.")
    return path


def resolve_capture(
    db: Session, *, capture_path: str | None = None, capture_id: int | None = None
) -> Path:
    if capture_id is not None:
        cap = db.get(Capture, capture_id)
        if cap is None or not cap.path:
            raise VoipAnalysisError("Capture nicht gefunden oder ohne Datei.")
        return validate_capture_path(Path(cap.path))
    if capture_path:
        return validate_capture_path(Path(capture_path))
    raise VoipAnalysisError("capture_path oder capture_id erforderlich.")


def _sip_pass(path: Path) -> list[str]:
    args = [
        settings.tshark_bin, "-r", str(path), "-Y", "sip",
        "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a",
    ]
    for field in SIP_FIELDS:
        args += ["-e", field]
    proc = run_sync(args, timeout=settings.tshark_timeout_s)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _rtp_pass(path: Path) -> str:
    proc = run_sync(
        [
            settings.tshark_bin, "-r", str(path), "-q",
            "-o", "rtp.heuristic_rtp:TRUE",
            "-z", "rtp,streams",
        ],
        timeout=settings.tshark_timeout_s,
    )
    return proc.stdout


def analyze_capture(path: Path) -> dict:
    """Analysiert eine (bereits validierte) PCAP-Datei. Rückgabe:
    ``{"sip": {...}, "codecs": {...}, "rtp": {...}, "error": str | None}``."""
    result: dict = {**_EMPTY, "error": None}
    try:
        sip = sip_parsers.parse_sip_messages(_sip_pass(path))
        streams = sip_parsers.parse_rtp_streams(_rtp_pass(path))
    except ToolNotFoundError as exc:
        return {**_EMPTY, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - Analyse darf den Aufrufer nie sprengen
        log.warning("VoIP-Analyse %s fehlgeschlagen: %s", path, exc)
        return {**_EMPTY, "error": f"Analyse fehlgeschlagen: {exc}"}

    result["sip"] = sip
    result["codecs"] = sip_parsers.codec_summary(sip)
    result["rtp"] = sip_parsers.rtp_summary(streams) | {"streams": streams[:50]}
    log.info(
        "VoIP-Analyse %s: %d SIP-Nachrichten, %d RTP-Streams",
        path.name, sip["messages_captured"], len(streams),
    )
    return result
