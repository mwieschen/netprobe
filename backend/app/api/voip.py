"""VoIP-/3CX-Monitoring: Server verwalten, Tests ausführen, Historie abrufen."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.timeutils import utcnow
from app.db.session import get_db, session_scope
from app.models.user import User
from app.models.voip import VoipServer, VoipTest
from app.schemas.schemas import (
    VoipServerCreate,
    VoipServerOut,
    VoipServerUpdate,
    VoipTestOut,
    VoipTestRequest,
)
from app.services import audit, voip_service
from app.workers.scheduler import scheduler_service

router = APIRouter(prefix="/api/voip", tags=["voip"])


def _test_to_out(test: VoipTest) -> VoipTestOut:
    try:
        webrtc = json.loads(test.webrtc_results_json or "[]")
    except (json.JSONDecodeError, TypeError):
        webrtc = []
    return VoipTestOut(
        id=test.id,
        server_id=test.server_id,
        ts=test.ts,
        ok=test.ok,
        source=test.source,
        sip_udp_ok=test.sip_udp_ok,
        sip_tcp_ok=test.sip_tcp_ok,
        sip_tls_ok=test.sip_tls_ok,
        webrtc_results=webrtc,
        sip_response_ms=test.sip_response_ms,
        sip_response_code=test.sip_response_code,
        tls_handshake_ms=test.tls_handshake_ms,
        tls_verified=test.tls_verified,
        tls_cert_expiry=test.tls_cert_expiry,
        loss_pct=test.loss_pct,
        rtt_avg_ms=test.rtt_avg_ms,
        registration_ok=test.registration_ok,
        registration_detail=test.registration_detail,
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

    username = payload.username
    password = payload.password

    def _job() -> int:
        with session_scope() as s:
            srv = s.get(VoipServer, server_id)
            if srv is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "3CX-Server nicht gefunden")
            test = voip_service.run_test(
                s, srv, source="manual", username=username, password=password
            )
            return test.id

    test_id = await asyncio.to_thread(_job)
    test = db.get(VoipTest, test_id)
    if test is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Testergebnis nicht gefunden")
    return _test_to_out(test)


@router.get("/servers/{server_id}/history", response_model=list[VoipTestOut])
def server_history(
    server_id: int,
    range: str = "24h",
    limit: int = 500,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[VoipTestOut]:
    since = _range_to_since(range)
    q = select(VoipTest).where(VoipTest.server_id == server_id).order_by(VoipTest.ts.desc())
    if since is not None:
        q = q.where(VoipTest.ts >= since)
    rows = db.execute(q.limit(min(limit, 5000))).scalars().all()
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


def _range_to_since(range: str):
    now = utcnow()
    mapping = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if range in mapping:
        return now - mapping[range]
    if range == "all":
        return None
    return now - timedelta(hours=24)
