"""MinIO object storage service."""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any

from minio import Minio
from minio.error import S3Error

from api.config import settings

_client: Minio | None = None


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
    client = get_minio_client()
    try:
        if not client.bucket_exists(settings.MINIO_BUCKET):
            client.make_bucket(settings.MINIO_BUCKET)
    except S3Error as e:
        print(f"MinIO bucket error: {e}")


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
    """Upload bytes to MinIO (for internal/worker use)."""
    client = get_minio_client()
    client.put_object(
        bucket_name=settings.MINIO_BUCKET,
        object_name=object_name,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def stat_object(object_name: str) -> dict[str, Any]:
    """Get object metadata from MinIO."""
    client = get_minio_client()
    stat = client.stat_object(settings.MINIO_BUCKET, object_name)
    return {
        "size": stat.size,
        "etag": stat.etag,
        "content_type": stat.content_type,
    }
