"""Private API-mediated book source and extracted-text viewer."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api.models import Book, BookStatus, IngestionJob, get_db_session
from api.services.object_store import get_object_store

router = APIRouter()

IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


@router.get("/books/{book_id}/images/{generation}/{image_id}.{ext}")
def get_book_image(
    book_id: str,
    generation: int,
    image_id: str,
    ext: str,
) -> StreamingResponse:
    normalized_ext = ext.lower()
    if generation < 1 or normalized_ext not in IMAGE_MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        uuid.UUID(book_id)
        uuid.UUID(image_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc

    with get_db_session() as db:
        book = (
            db.query(Book)
            .filter(
                Book.uuid == book_id,
                Book.deleted_at.is_(None),
                Book.status == BookStatus.INDEXED,
                Book.indexed_generation == generation,
            )
            .first()
        )
        if book is None:
            raise HTTPException(status_code=404, detail="Image not found")

    object_key = (
        f"books/{book_id}/generations/{generation}/images/{image_id}.{normalized_ext}"
    )
    store = get_object_store()
    try:
        store.stat(object_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    return StreamingResponse(
        store.iter_bytes(object_key),
        media_type=IMAGE_MEDIA_TYPES[normalized_ext],
    )


def _content_disposition(disposition: str, filename: str) -> str:
    safe = filename.replace("\r", "").replace("\n", "").replace('"', "")
    return f"{disposition}; filename*=UTF-8''{quote(safe)}"


@router.get("/book/{book_id}/pdf")
def get_book_pdf(book_id: str) -> StreamingResponse:
    """Stream a private original source through the API."""
    with get_db_session() as db:
        book = (
            db.query(Book)
            .filter(Book.uuid == book_id, Book.deleted_at.is_(None))
            .first()
        )
        if book is None or not book.source_object_key:
            raise HTTPException(status_code=404, detail="Book source not found")
        source_key = book.source_object_key
        filename = book.original_filename
        media_type = (
            "application/pdf"
            if book.file_extension == ".pdf"
            else "application/octet-stream"
        )

    store = get_object_store()
    try:
        info = store.stat(source_key)
    except Exception:
        raise HTTPException(
            status_code=404, detail="File not found in storage"
        ) from None
    return StreamingResponse(
        store.iter_bytes(source_key),
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition("inline", filename),
            "Content-Length": str(info.size),
        },
    )


@router.get("/book/{book_id}/markdown")
def get_book_markdown(book_id: str) -> StreamingResponse:
    """Stream the latest durable extracted-text artifact."""
    with get_db_session() as db:
        book = (
            db.query(Book)
            .filter(Book.uuid == book_id, Book.deleted_at.is_(None))
            .first()
        )
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        job = (
            db.query(IngestionJob)
            .filter(IngestionJob.book_id == book.id)
            .order_by(IngestionJob.generation.desc())
            .first()
        )
        if job is None or not job.extracted_text_key:
            raise HTTPException(status_code=404, detail="No extracted text available")
        artifact_key = job.extracted_text_key
        filename = f"{book.title}.txt"

    store = get_object_store()
    if not store.exists(artifact_key):
        raise HTTPException(status_code=404, detail="Extracted text artifact not found")
    return StreamingResponse(
        store.iter_bytes(artifact_key),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition("inline", filename)},
    )


@router.get("/book/{book_id}")
def get_book_info(book_id: str) -> dict[str, Any]:
    """Get book metadata without exposing private storage credentials."""
    with get_db_session() as db:
        book = (
            db.query(Book)
            .filter(Book.uuid == book_id, Book.deleted_at.is_(None))
            .first()
        )
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return {
            "uuid": book.uuid,
            "title": book.title,
            "author": book.author,
            "original_filename": book.original_filename,
            "status": book.status,
            "file_extension": book.file_extension,
            "file_size_bytes": book.file_size_bytes,
            "total_chunks": book.total_chunks,
            "indexed_chunks": book.indexed_chunks,
            "source_available": bool(book.source_object_key),
        }
