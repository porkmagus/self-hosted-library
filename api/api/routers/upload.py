"""Private API-mediated source upload and download endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from api.models import Book, get_db
from api.services.ingestion_submit import submit_uploaded_book
from api.services.object_store import get_object_store
from api.services.path_svc import validate_filename

router = APIRouter(prefix="/upload")
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".htm",
    ".html",
}


class UploadResponse(BaseModel):
    book_uuid: str
    job_uuid: str
    task_id: str
    status: str


class UploadRequest(BaseModel):
    """Compatibility schema retaining strict plain-filename validation."""

    filename: str
    content_type: str = "application/octet-stream"

    @field_validator("filename")
    @classmethod
    def filename_is_plain(cls, value: str) -> str:
        return validate_filename(value)


@router.post("", response_model=UploadResponse, status_code=202)
def upload_source(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Spool multipart input, then stream it into private durable object storage."""
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported file type: {extension}"
        )
    try:
        result = submit_uploaded_book(
            db,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            stream=file.file,
            store=get_object_store(),
            title=title.strip() if title and title.strip() else None,
            author=author.strip() if author and author.strip() else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Upload could not be persisted"
        ) from exc
    return UploadResponse(
        book_uuid=result.book_uuid,
        job_uuid=result.job_uuid,
        task_id=result.task_id,
        status="queued",
    )


@router.get("/{book_uuid}/source")
def download_source(book_uuid: str, db: Session = Depends(get_db)) -> FileResponse:
    """Stream a private source through the API without exposing S3 credentials."""
    book = (
        db.query(Book).filter(Book.uuid == book_uuid, Book.deleted_at.is_(None)).first()
    )
    if book is None or not book.source_object_key:
        raise HTTPException(status_code=404, detail="Book source not found")
    store = get_object_store()
    if not store.exists(book.source_object_key):
        raise HTTPException(status_code=404, detail="Book source object not found")

    with tempfile.NamedTemporaryFile(
        prefix="grimoire-download-", delete=False
    ) as temporary:
        path = Path(temporary.name)
    try:
        store.download_to(book.source_object_key, path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503, detail="Book source is unavailable"
        ) from exc
    return FileResponse(
        path,
        filename=book.original_filename,
        media_type="application/octet-stream",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
