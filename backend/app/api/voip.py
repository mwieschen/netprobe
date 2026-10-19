"""VoIP-/3CX-Monitoring: Server verwalten, Tests ausführen, Historie abrufen."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.api.timerange import resolve_range, thinned_rows
from app.core.config import settings
from app.db.session import get_db, session_scope
from app.models.user import User
from app.models.voip import VoipServer, VoipTest
from app.schemas.schemas import (
    VoipAnalysisOut,
    VoipAnalyzeRequest,
    VoipServerCreate,
    VoipServerOut,
    VoipServerUpdate,
    VoipTestOut,
    VoipTestRequest,
)
from app.services import audit, capture_service, voip_analysis, voip_service
from app.services.proc import ToolNotFoundError
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/voip", tags=["voip"])


def _jload(raw: str | None, default):
    try:
        return json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default


def _test_to_out(test: VoipTest) -> VoipTestOut:
    return VoipTestOut(
        id=test.id,
        server_id=test.server_id,
        ts=test.ts,
        ok=test.ok,
        source=test.source,
        sip_udp_ok=test.sip_udp_ok,
        sip_tcp_ok=test.sip_tcp_ok,
        sip_tls_ok=test.sip_tls_ok,
        webrtc_results=_jload(test.webrtc_results_json, []),
        sip_response_ms=test.sip_response_ms,
        sip_response_code=test.sip_response_code,
        tls_handshake_ms=test.tls_handshake_ms,
        tls_verified=test.tls_verified,
        tls_cert_expiry=test.tls_cert_expiry,
        loss_pct=test.loss_pct,
        rtt_avg_ms=test.rtt_avg_ms,
        registration_ok=test.registration_ok,
        registration_detail=test.registration_detail,
        server_header=test.server_header,
        supported_methods=test.supported_methods,
        tls_version=test.tls_version,
        tls_cipher=test.tls_cipher,
        cert_subject=test.cert_subject,
        cert_issuer=test.cert_issuer,
        capture_path=test.capture_path,
        capture_url=f"/api/voip/tests/{test.id}/capture" if test.capture_path else None,
        sip_analysis=_jload(test.sip_analysis_json, {}),
        codec_analysis=_jload(test.codec_analysis_json, {}),
        rtp_analysis=_jload(test.rtp_analysis_json, {}),
        error=test.error,
    )


def _server_to_out(db: Session, server: VoipServer) -> VoipServerOut:
    last = db.execute(
        select(VoipTest)
        .where(VoipTest.server_id == server.id)
        .order_by(VoipTest.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    return VoipServerOut(
        id=server.id,
        name=server.name,
        host=server.host,
        sip_port=server.sip_port,
        sip_tls_port=server.sip_tls_port,
        webrtc_ports=server.webrtc_ports,
        sip_transport=server.sip_transport,
        check_registration=server.check_registration,
        enabled=server.enabled,
        interval_s=server.interval_s,
        created_at=server.created_at,
        last_test=_test_to_out(last) if last else None,
    )


@router.get("/servers", response_model=list[VoipServerOut])
def list_servers(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[VoipServerOut]:
    servers = db.execute(select(VoipServer).order_by(VoipServer.id)).scalars().all()
    return [_server_to_out(db, s) for s in servers]


@router.post("/servers", response_model=VoipServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    payload: VoipServerCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> VoipServerOut:
    server = VoipServer(**payload.model_dump())
    db.add(server)
    audit.record(db, "voip_server_create", user_id=user.id, username=user.username,
                 detail=payload.name, ip=client_ip(request))
    db.commit()
    db.refresh(server)
    scheduler_service.sync_voip_jobs()
    return _server_to_out(db, server)


@router.patch("/servers/{server_id}", response_model=VoipServerOut)
def update_server(
    server_id: int,
    payload: VoipServerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> VoipServerOut:
    server = db.get(VoipServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "3CX-Server nicht gefunden")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(server, k, v)
    audit.record(db, "voip_server_update", user_id=user.id, username=user.username, detail=str(server_id))
    db.commit()
    db.refresh(server)
    scheduler_service.sync_voip_jobs()
    return _server_to_out(db, server)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    server = db.get(VoipServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "3CX-Server nicht gefunden")
    db.delete(server)
    audit.record(db, "voip_server_delete", user_id=user.id, username=user.username, detail=str(server_id))
    db.commit()
    scheduler_service.sync_voip_jobs()


@router.post(
    "/servers/{server_id}/test",
    response_model=VoipTestOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_test_now(
    server_id: int,
    payload: VoipTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> VoipTestOut:
    server = db.get(VoipServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "3CX-Server nicht gefunden")

    audit.record(db, "voip_test_run", user_id=user.id, username=user.username,
                 detail=str(server_id), ip=client_ip(request))
    db.commit()

    p = payload

    def _job() -> int:
        with session_scope() as s:
            srv = s.get(VoipServer, server_id)
            if srv is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "3CX-Server nicht gefunden")
            return voip_service.run_test(
                s, srv, source="manual",
                username=p.username, password=p.password,
                capture_interface=p.capture_interface,
                capture_duration_s=p.capture_duration_s,
                capture_bpf=p.capture_bpf,
            ).id

    test_id = await asyncio.to_thread(_job)
    test = db.get(VoipTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Testergebnis nicht gefunden")
    return _test_to_out(test)


@router.get("/interfaces")
def list_interfaces(_: User = Depends(require_admin)) -> list[dict]:
    """Capture-Interfaces für den optionalen Test-Mitschnitt (``dumpcap -D``)."""
    try:
        return capture_service.list_interfaces()
    except ToolNotFoundError:
        return []


@router.post("/analyze", response_model=VoipAnalysisOut)
async def analyze_capture(
    payload: VoipAnalyzeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> VoipAnalysisOut:
    """Analysiert eine **vorhandene** Capture auf SIP/SDP/RTP (nicht persistiert)."""
    if payload.capture_id is None and not payload.capture_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "capture_id oder capture_path erforderlich.")

    audit.record(db, "voip_analyze", user_id=user.id, username=user.username,
                 detail=str(payload.capture_id or payload.capture_path), ip=client_ip(request))
    db.commit()

    def _job() -> dict:
        with session_scope() as s:
            path = voip_analysis.resolve_capture(
                s, capture_path=payload.capture_path, capture_id=payload.capture_id
            )
            return voip_analysis.analyze_capture(path)

    try:
        res = await asyncio.to_thread(_job)
    except voip_analysis.VoipAnalysisError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return VoipAnalysisOut(**res)


@router.get("/tests/{test_id}/capture")
def download_capture(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Roh-PCAP eines VoIP-Test-Mitschnitts. **Nur Admin** – der Mitschnitt kann
    Gesprächsaudio und SIP-Auth-Material enthalten (Viewer sehen nur die
    aggregierte JSON-Analyse in ``VoipTestOut``)."""
    test = db.get(VoipTest, test_id)
    if test is None or not test.capture_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kein Mitschnitt für diesen Test.")
    path = Path(test.capture_path)
    try:
        path.resolve().relative_to(settings.capture_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Capture-Pfad.") from exc
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mitschnitt-Datei fehlt.")
    audit.record(db, "voip_capture_download", user_id=user.id, username=user.username,
                 detail=str(test_id), ip=client_ip(request))
    db.commit()
    return FileResponse(
        path,
        media_type="application/vnd.tcpdump.pcap",
        filename=path.name,
        content_disposition_type="attachment",
    )


@router.get("/servers/{server_id}/history", response_model=list[VoipTestOut])
def server_history(
    server_id: int,
    range: str = "24h",
    start: str | None = None,
    end: str | None = None,
    limit: int = 2000,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[VoipTestOut]:
    since, until = resolve_range(range, start, end, default="24h")
    conds = [VoipTest.server_id == server_id]
    if since is not None:
        conds.append(VoipTest.ts >= since)
    if until is not None:
        conds.append(VoipTest.ts <= until)
    rows = thinned_rows(db, VoipTest, conds, VoipTest.ts, min(max(limit, 1), 5000))
    return [_test_to_out(t) for t in rows]


@router.get("/history", response_model=list[VoipTestOut])
def all_history(
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[VoipTestOut]:
    rows = db.execute(
        select(VoipTest).order_by(VoipTest.ts.desc()).limit(min(limit, 1000))
    ).scalars().all()
    return [_test_to_out(t) for t in rows]

