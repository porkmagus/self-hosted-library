"""Self-hosted library API entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.models import init_db
from api.routers import (
    book_viewer,
    books,
    context,
    health,
    ingest,
    media,
    search,
    settings,
    upload,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    init_minio_bucket()
    init_qdrant_collection()
    init_image_collection()
    _init_settings()
    yield


def init_minio_bucket() -> None:
    from api.services.minio_svc import ensure_bucket
    ensure_bucket()


def init_qdrant_collection() -> None:
    from api.services.qdrant_svc import init_collection
    init_collection()


def init_image_collection() -> None:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(books.router, prefix="/api", tags=["books"])
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(ingest.router, prefix="/api", tags=["ingest"])
app.include_router(book_viewer.router, prefix="/api", tags=["viewer"])
app.include_router(media.router, prefix="/api/media", tags=["media"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
