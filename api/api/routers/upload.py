"""Upload router — presigned URLs for direct browser-to-MinIO uploads."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.config import settings
from api.services.minio_svc import ensure_bucket, get_presigned_upload_url

router = APIRouter()


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class UploadResponse(BaseModel):
    upload_url: str
    object_key: str
    file_id: str


@router.post("/upload/presign")
async def get_presigned_url(req: UploadRequest) -> UploadResponse:
    """Get a presigned URL for direct browser upload to MinIO."""
    ensure_bucket()

    # Generate a unique object key
    file_id = str(uuid.uuid4())
    object_key = f"{file_id}_{req.filename}"

    # Get presigned PUT URL (valid for 1 hour)
    upload_url = get_presigned_upload_url(object_key, expires_seconds=3600)

    return UploadResponse(
        upload_url=upload_url,
        object_key=object_key,
        file_id=file_id,
    )


@router.post("/upload/confirm")
async def confirm_upload(file_id: str, filename: str) -> dict[str, Any]:
    """Called by the frontend after upload completes to trigger ingestion."""

    from api.models import Book, BookStatus, get_db
    from api.services.minio_svc import get_minio_client
    from api.tasks.celery_app import ingest_book_task

    # Stat the file in MinIO to confirm it exists
    client = get_minio_client()
    object_key = f"{file_id}_{filename}"
    try:
        client.stat_object(settings.MINIO_BUCKET, object_key)
    except Exception:
        raise HTTPException(
            status_code=404, detail=f"File {filename} not found in MinIO"
        ) from None

    # Create a book record and queue ingestion
    db = next(iter(get_db()))
    try:
        book_uuid = str(uuid.uuid4())
        book = Book(
            uuid=book_uuid,
            title=filename.replace(".", " ").title(),
            original_filename=filename,
            sanitized_filename=filename[:150],
            file_extension=filename.rsplit(".", 1)[-1]
            if "." in filename
            else "unknown",
            minio_object_key=object_key,
            status=BookStatus.PENDING,
        )
        db.add(book)
        db.commit()

        # Queue Celery task for ingestion
        # The worker will download from MinIO first
        task = ingest_book_task.delay(object_key)

        return {
            "book_id": book_uuid,
            "task_id": task.id,
            "status": "queued",
        }
    finally:
        db.close()
