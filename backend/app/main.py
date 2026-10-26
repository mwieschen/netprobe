"""FastAPI-Anwendung: Router-Registrierung, Lifespan (Scheduler + Ping-Manager),
statische Auslieferung des Frontend-Builds."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, JSONResponse

from app import __version__
from app.api import (
    audit_api,
    auth,
    capture_jobs,
    captures,
    clients,
    clients_notify,
    diagnostics,
    health,
    health_checks,
    iperf3,
    mtu,
    ping,
    reports,
    retransmission,
    scheduler_api,
    settings_api,
    speedtest,
    traceroute,
    users,
    voip,
    wachtl_probe,
    wan,
)
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.services.iperf_service import iperf3_server
from app.services.long_capture import long_capture_manager
from app.services.ping_manager import ping_manager
from app.services.rtp_reflector import rtp_reflector
from app.workers.scheduler import scheduler_service

setup_logging()
log = get_logger("netprobe.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    log.info("netprobe %s startet", __version__)
    loop = asyncio.get_running_loop()
    ping_manager.bind_loop(loop)
    long_capture_manager.bind_loop(loop)
    rtp_reflector.bind_loop(loop)
    scheduler_service.start()
    ping_manager.resume_all()
    long_capture_manager.resume_all()
    iperf3_server.resume()
    await rtp_reflector.resume()
    try:
        yield
    finally:
        log.info("netprobe fährt herunter")
        scheduler_service.shutdown()
        await ping_manager.shutdown()
        await long_capture_manager.shutdown()
        await iperf3_server.shutdown()
        await rtp_reflector.shutdown()


app = FastAPI(
    title="netprobe API",
    version=__version__,
    description="Lokale Web-Appliance für Netzwerk- und WAN-Monitoring.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

for router in (
    health.router,
    auth.router,
    speedtest.router,
    wan.router,
    ping.router,
    captures.router,
    capture_jobs.router,
    iperf3.router,
    mtu.router,
    reports.router,
    voip.router,
    traceroute.router,
    health_checks.router,
    retransmission.router,
    scheduler_api.router,
    diagnostics.router,
    settings_api.router,
    users.router,
    audit_api.router,
    clients.router,
    clients_notify.router,
    wachtl_probe.router,
):
    app.include_router(router)

# Wachtl-Probes erwarten Fehler als {"error": "…"} statt FastAPIs {"detail": "…"}.
app.add_exception_handler(wachtl_probe.WachtlApiError, wachtl_probe.wachtl_error_handler)


# --- Statisches Frontend (Single Page App) ---
_dist = settings.frontend_dist
if _dist.exists() and (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    _dist_root = _dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Path-Traversal verhindern: aufgelöster Pfad muss innerhalb von dist liegen.
        candidate = (_dist_root / full_path).resolve()
        try:
            candidate.relative_to(_dist_root)
        except ValueError:
            return FileResponse(_dist_root / "index.html")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist_root / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse(
            {
                "app": "netprobe",
                "version": __version__,
                "docs": "/api/docs",
                "note": "Frontend-Build nicht gefunden. Bitte 'npm run build' im frontend/ ausführen.",
            }
        )
