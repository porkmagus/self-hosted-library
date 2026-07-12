"""Health check router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.config import settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health endpoint — checks all service dependencies."""
    status: dict[str, Any] = {"status": "ok", "version": "2.0.0"}
    errors: list[str] = []

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.OLLAMA_URL}/api/tags")
            status["ollama"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        status["ollama"] = "unreachable"
        errors.append("Ollama unreachable")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.QDRANT_URL}/collections")
            status["qdrant"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        status["qdrant"] = "unreachable"
        errors.append("Qdrant unreachable")

    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.close()
        status["redis"] = "ok"
    except Exception:
        status["redis"] = "unreachable"
        errors.append("Redis unreachable")

    try:
        from sqlalchemy import text

        from api.models import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception:
        status["postgres"] = "unreachable"
        errors.append("PostgreSQL unreachable")

    try:
        from api.services.minio_svc import get_minio_client

        minio_client = get_minio_client()
        minio_client.bucket_exists(settings.MINIO_BUCKET)
        status["minio"] = "ok"
    except Exception:
        status["minio"] = "unreachable"
        errors.append("MinIO unreachable")

    status["warnings"] = errors
    return status
