"""Upload router — presigned URLs for direct browser-to-MinIO uploads."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from api.config import settings
from api.models import Book, BookStatus, get_db_session
from api.services.minio_svc import ensure_bucket, get_presigned_upload_url
from api.services.path_svc import validate_filename

router = APIRouter()


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"

    @field_validator("filename")
    @classmethod
    def filename_must_be_plain(cls, value: str) -> str:
        return validate_filename(value)


class UploadResponse(BaseModel):
    upload_url: str
    object_key: str
    file_id: str


@router.post("/upload/presign")
def get_presigned_url(req: UploadRequest) -> UploadResponse:
    """Get a presigned URL for direct browser upload to MinIO."""
    ensure_bucket()

    file_id = str(uuid.uuid4())
    object_key = f"{file_id}_{req.filename}"

    upload_url = get_presigned_upload_url(object_key, expires_seconds=3600)

    return UploadResponse(
        upload_url=upload_url,
        object_key=object_key,
        file_id=file_id,
    )


@router.post("/upload/confirm")
def confirm_upload(file_id: str, filename: str) -> dict[str, Any]:
    """Called by the frontend after upload completes to trigger ingestion."""

    from api.services.minio_svc import get_minio_client
    from api.tasks.celery_app import ingest_book_task

    try:
        filename = validate_filename(filename)
        file_id = str(uuid.UUID(file_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = get_minio_client()
    object_key = f"{file_id}_{filename}"
    try:
        client.stat_object(settings.MINIO_BUCKET, object_key)
    except Exception:
        raise HTTPException(
            status_code=404, detail=f"File {filename} not found in MinIO"
        ) from None

    with get_db_session() as db:
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

        task = ingest_book_task.delay(object_key)

        return {
            "book_id": book_uuid,
            "task_id": task.id,
            "status": "queued",
        }
