"""Packet-Capture-Endpunkte: erstellen, auflisten, analysieren, herunterladen,
Display-Filter, CSV/JSON-Export."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.config import settings
from app.db.session import get_db, session_scope
from app.models.capture import Capture
from app.models.user import User
from app.schemas.schemas import CaptureCreate, CaptureOut, DisplayFilterRequest
from app.services import audit, capture_service
from app.services.capture_service import CaptureValidationError
from app.services.proc import ToolNotFoundError

router = APIRouter(prefix="/api/captures", tags=["captures"])


@router.get("/interfaces")
def interfaces(_: User = Depends(require_admin)) -> list[dict]:
    try:
        return capture_service.list_interfaces()
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("", response_model=list[CaptureOut])
def list_captures(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[Capture]:
    return list(db.execute(select(Capture).order_by(Capture.ts.desc())).scalars().all())


@router.post("", response_model=CaptureOut, status_code=status.HTTP_201_CREATED)
async def create_capture(
    payload: CaptureCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Capture:
    duration_s, size_mb = capture_service.clamp_limits(
        payload.duration_s, payload.max_filesize_mb or settings.capture_default_size_mb
    )
    try:
        capture_service.validate_iface(payload.iface)
        capture_service.validate_bpf(payload.bpf)
    except CaptureValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    capture = Capture(
        iface=payload.iface,
        status="running",
        duration_s=duration_s,
        max_packets=payload.max_packets,
        max_filesize_mb=size_mb,
        ringbuffer=payload.ringbuffer,
        bpf=payload.bpf,
    )
    db.add(capture)
    audit.record(db, "capture_create", user_id=user.id, username=user.username,
                 detail=f"iface={payload.iface} dur={duration_s}s", ip=client_ip(request))
    db.commit()
    db.refresh(capture)
    capture_id = capture.id

    def _job() -> None:
        with session_scope() as s:
            cap = s.get(Capture, capture_id)
            if cap is not None:
                capture_service.run_capture(s, cap)

    await asyncio.to_thread(_job)
    db.refresh(capture)
    return capture


@router.get("/{capture_id}", response_model=CaptureOut)
def get_capture(
    capture_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Capture:
    cap = db.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture nicht gefunden")
    return cap


def _require_pcap(db: Session, capture_id: int) -> tuple[Capture, Path]:
    cap = db.get(Capture, capture_id)
    if cap is None or not cap.path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture nicht gefunden")
    path = Path(cap.path)
    # Sicherstellen, dass die Datei tatsächlich im Capture-Verzeichnis liegt
    try:
        path.resolve().relative_to(settings.capture_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Pfad") from exc
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture-Datei fehlt")
    return cap, path


@router.get("/{capture_id}/download")
def download(capture_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _cap, path = _require_pcap(db, capture_id)
    return FileResponse(path, media_type="application/vnd.tcpdump.pcap", filename=path.name)


@router.get("/{capture_id}/analysis")
async def analysis(
    capture_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    _cap, path = _require_pcap(db, capture_id)
    try:
        return await asyncio.to_thread(capture_service.analyze, path)
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/{capture_id}/analysis.json")
async def analysis_json(capture_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    data = await analysis(capture_id, db, _)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    return Response(
        payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="analysis_{capture_id}.json"'},
    )


@router.get("/{capture_id}/analysis.csv")
async def analysis_csv(capture_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    data = await analysis(capture_id, db, _)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "value"])
    writer.writerow(["summary", "packet_count", data.get("packet_count")])
    writer.writerow(["summary", "tcp_retransmissions", data.get("tcp_retransmissions")])
    writer.writerow(["summary", "tcp_resets", data.get("tcp_resets")])
    for ep in data.get("top_endpoints", []):
        writer.writerow(["endpoint", ep["address"], ep["bytes"]])
    for q in data.get("dns_queries", []):
        writer.writerow(["dns", q["name"], q["count"]])
    for s in data.get("tls_sni", []):
        writer.writerow(["tls_sni", s["server_name"], s["count"]])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="analysis_{capture_id}.csv"'},
    )


@router.post("/{capture_id}/filter")
async def display_filter(
    capture_id: int,
    payload: DisplayFilterRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    _cap, path = _require_pcap(db, capture_id)
    try:
        return await asyncio.to_thread(
            capture_service.apply_display_filter, path, payload.filter, payload.offset, payload.limit
        )
    except CaptureValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/{capture_id}/packets")
async def packets(
    capture_id: int,
    filter: str = "",
    offset: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Gefilterte Paketliste (JSON) im Wireshark-Stil. ``filter`` ist ein
    tshark-Display-Filter; leer = alle Pakete. Ergebnis wird auf
    ``display_filter_max_packets`` begrenzt (mit ``offset``/``limit`` Paging)."""
    _cap, path = _require_pcap(db, capture_id)
    limit = max(1, min(limit, settings.display_filter_max_packets))
    offset = max(0, offset)
    try:
        return await asyncio.to_thread(
            capture_service.apply_display_filter, path, filter, offset, limit
        )
    except CaptureValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/{capture_id}/conversations")
async def conversations(
    capture_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """IP-Conversations (``tshark -q -z conv,ip``)."""
    _cap, path = _require_pcap(db, capture_id)
    try:
        rows = await asyncio.to_thread(capture_service.conversations_ip, path)
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"conversations": rows}


@router.get("/{capture_id}/stats")
async def capture_stats(
    capture_id: int,
    buckets: int = 120,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """I/O-Statistik über die Zeit (Pakete/s, Bit/s) plus Gesamtwerte."""
    _cap, path = _require_pcap(db, capture_id)
    buckets = max(10, min(buckets, 500))
    try:
        return await asyncio.to_thread(capture_service.capture_stats, path, buckets)
    except ToolNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.delete("/{capture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_capture(
    capture_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    cap = db.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture nicht gefunden")
    if cap.path:
        try:
            p = Path(cap.path)
            p.resolve().relative_to(settings.capture_dir.resolve())
            p.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
    db.delete(cap)
    audit.record(db, "capture_delete", user_id=user.id, username=user.username, detail=str(capture_id))
    db.commit()
