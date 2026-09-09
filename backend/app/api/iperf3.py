"""iperf3-Endpunkte: Client-Test, Ergebnisverlauf, Server-Steuerung."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db, session_scope
from app.models.iperf import IperfResult
from app.models.user import User
from app.schemas.schemas import (
    Iperf3ClientRequest,
    Iperf3ResultOut,
    Iperf3ServerRequest,
    Iperf3ServerStatus,
)
from app.services import audit, iperf_service
from app.services.iperf_service import iperf3_server

router = APIRouter(prefix="/api/iperf3", tags=["iperf3"])


@router.get("/results", response_model=list[Iperf3ResultOut])
def list_results(
    limit: int = 100, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[IperfResult]:
    return list(
        db.execute(
            select(IperfResult).order_by(IperfResult.ts.desc()).limit(min(limit, 1000))
        ).scalars().all()
    )


@router.post("/client", response_model=Iperf3ResultOut, status_code=status.HTTP_201_CREATED)
async def run_client(
    payload: Iperf3ClientRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> IperfResult:
    audit.record(db, "iperf3_client", user_id=user.id, username=user.username,
                 detail=f"{payload.host}:{payload.port}", ip=client_ip(request))
    db.commit()

    def _job() -> int:
        with session_scope() as s:
            result = iperf_service.run_client(
                s,
                host=payload.host,
                port=payload.port,
                duration=payload.duration_s,
                parallel=payload.parallel,
                udp=payload.udp,
                reverse=payload.reverse,
            )
            return result.id

    result_id = await asyncio.to_thread(_job)
    result = db.get(IperfResult, result_id)
    if result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Ergebnis nicht gefunden")
    return result


@router.get("/server/status", response_model=Iperf3ServerStatus)
def server_status(_: User = Depends(get_current_user)) -> Iperf3ServerStatus:
    return Iperf3ServerStatus(running=iperf3_server.is_running(), port=iperf3_server.port)


@router.post("/server", response_model=Iperf3ServerStatus)
async def server_control(
    payload: Iperf3ServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Iperf3ServerStatus:
    try:
        await iperf3_server.apply(db, enabled=payload.enabled, port=payload.port)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Tool fehlt / Startfehler
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    audit.record(db, "iperf3_server", user_id=user.id, username=user.username,
                 detail=f"enabled={payload.enabled} port={iperf3_server.port}", ip=client_ip(request))
    db.commit()
    return Iperf3ServerStatus(running=iperf3_server.is_running(), port=iperf3_server.port)
