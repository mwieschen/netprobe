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
    captures,
    health,
    ping,
    settings_api,
    speedtest,
    users,
    wan,
)
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.services.ping_manager import ping_manager
from app.workers.scheduler import scheduler_service

setup_logging()
log = get_logger("netprobe.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    log.info("netprobe %s startet", __version__)
    ping_manager.bind_loop(asyncio.get_running_loop())
    scheduler_service.start()
    ping_manager.resume_all()
    try:
        yield
    finally:
        log.info("netprobe fährt herunter")
        scheduler_service.shutdown()
        await ping_manager.shutdown()


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
    settings_api.router,
    users.router,
    audit_api.router,
):
    app.include_router(router)


# --- Statisches Frontend (Single Page App) ---
_dist = settings.frontend_dist
if _dist.exists() and (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = _dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")

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
