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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
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

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve React SPA — fallback to index.html for client-side routing."""
        path = STATIC_DIR / full_path
        if path.is_file():
            return FileResponse(path)
        return FileResponse(STATIC_DIR / "index.html")
