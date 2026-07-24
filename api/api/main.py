"""Self-hosted library API entry point — serves both API and React frontend."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from api.config import settings as app_settings
from api.models import get_engine, init_db
from api.routers import (
    book_viewer,
    context,
    health,
    ingest,
    media,
    search,
    settings,
    upload,
)

logger = logging.getLogger(__name__)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await anyio.to_thread.run_sync(init_db)
    await anyio.to_thread.run_sync(_init_minio_bucket)
    await anyio.to_thread.run_sync(_init_qdrant_collection)
    await anyio.to_thread.run_sync(_init_image_collection)
    await anyio.to_thread.run_sync(_init_settings)
    yield
    engine = get_engine()
    engine.dispose()
    logger.info("Database engine disposed")


def _init_minio_bucket() -> None:
    from api.services.minio_svc import ensure_bucket

    ensure_bucket()


def _init_qdrant_collection() -> None:
    from api.services.qdrant_svc import init_collection

    init_collection()


def _init_image_collection() -> None:
    from api.services.image_svc import init_image_collection

    init_image_collection()


def _init_settings() -> None:
    from api.services.settings_svc import init_settings_table

    init_settings_table()


app = FastAPI(
    title="Self-Hosted Library API",
    description="Private document ingestion, retrieval, and research API",
    version="2.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Any:
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_request_id
        if 1 <= len(supplied_request_id) <= 64
        and supplied_request_id.replace("-", "").replace("_", "").isalnum()
        else uuid.uuid4().hex[:12]
    )
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


@app.exception_handler(Exception)
async def error_envelope_handler(request: Request, exc: Exception) -> JSONResponse:
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"HTTP_{exc.status_code}",
                    "message": str(exc.detail),
                }
            },
            headers=getattr(exc, "headers", None) or {},
        )
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(ingest.router, prefix="/api", tags=["ingest"])
app.include_router(book_viewer.router, prefix="/api", tags=["viewer"])
app.include_router(media.router, prefix="/api/media", tags=["media"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

# ── Serve React SPA ─────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class StaticCacheControlMiddleware(BaseHTTPMiddleware):
    """Disable caching for the SPA entry point and hashed assets.

    Browsers aggressively cache index.html, which pins the old hashed JS/CSS
    filenames even after a deployment. The app container rebuilds the web bundle
    on every image build; therefore the server must tell clients to revalidate.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith(".html") or "/assets/" in path:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


if STATIC_DIR.is_dir():
    app.mount(
        "/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets"
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve React SPA — fallback to index.html for client-side routing."""
        path = STATIC_DIR / full_path
        if path.is_file():
            return FileResponse(path)
        return FileResponse(STATIC_DIR / "index.html")

    app.add_middleware(StaticCacheControlMiddleware)
