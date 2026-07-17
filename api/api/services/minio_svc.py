"""MinIO object storage service."""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from minio import Minio

from api.config import settings
from api.services.object_store import get_object_store

_client: Minio | None = None


def build_public_object_url(object_name: str) -> str:
    """Build a browser-safe URL without persisting instance hostnames in indexes."""
    base = settings.MINIO_PUBLIC_URL.rstrip("/")
    bucket = quote(settings.MINIO_BUCKET, safe="")
    key = quote(object_name, safe="/")
    return f"{base}/{bucket}/{key}"


def get_minio_client() -> Minio:
    """Get or create MinIO client singleton."""
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=False,
        )
    return _client


def ensure_bucket() -> None:
    """Create the books bucket if it doesn't exist."""
    get_object_store().ensure_bucket()


def get_presigned_upload_url(object_name: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned PUT URL for direct browser uploads."""
    client = get_minio_client()
    return client.presigned_put_object(
        bucket_name=settings.MINIO_BUCKET,
        object_name=object_name,
        expires=timedelta(seconds=expires_seconds),
    )


def get_presigned_download_url(object_name: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned GET URL for downloads."""
    client = get_minio_client()
    return client.presigned_get_object(
        bucket_name=settings.MINIO_BUCKET,
        object_name=object_name,
        expires=timedelta(seconds=expires_seconds),
    )


def upload_object(
    object_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    """Compatibility wrapper for internal object-store uploads."""
    get_object_store().upload_stream(
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def stat_object(object_name: str) -> dict[str, Any]:
    """Compatibility wrapper for private object metadata."""
    stat = get_object_store().stat(object_name)
    return {
        "size": stat.size,
        "etag": stat.etag,
        "content_type": stat.content_type,
    }
