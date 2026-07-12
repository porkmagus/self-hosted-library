"""Book viewer router — serve PDFs and markdown from MinIO."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from api.config import settings
from api.models import get_db_session
from api.services.minio_svc import get_minio_client

router = APIRouter()


@router.get("/book/{book_id}/pdf")
async def get_book_pdf(book_id: str) -> StreamingResponse:
    """Stream a book's original PDF from MinIO."""
    with get_db_session() as db:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT minio_object_key, title FROM books WHERE uuid = :bid"),
            {"bid": book_id},
        ).fetchone()

    if not row or not row[0]:
        raise HTTPException(
            status_code=404, detail="Book not found or no PDF available"
        )

    minio = get_minio_client()
    try:
        stat = minio.stat_object(settings.MINIO_BUCKET, row[0])
    except Exception:
        raise HTTPException(
            status_code=404, detail="File not found in storage"
        ) from None

    def stream() -> Any:
        response = minio.get_object(settings.MINIO_BUCKET, row[0])
        try:
            yield from response.stream(8192)
        finally:
            response.close()

    return StreamingResponse(
        stream(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{row[1] or "book"}.pdf"',
            "Content-Length": str(stat.size),
        },
    )


@router.get("/book/{book_id}/markdown")
async def get_book_markdown(book_id: str) -> Response:
    """Return parsed markdown for a book if available."""
    with get_db_session() as db:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT markdown_path, title FROM books WHERE uuid = :bid"),
            {"bid": book_id},
        ).fetchone()

    if not row or not row[0]:
        raise HTTPException(
            status_code=404, detail="No markdown available for this book"
        )

    md_path = row[0]
    try:
        with open(md_path) as f:
            content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Markdown file not found") from None

    return Response(content, media_type="text/markdown")


@router.get("/book/{book_id}")
async def get_book_info(book_id: str) -> dict[str, Any]:
    """Get book metadata."""
    with get_db_session() as db:
        from sqlalchemy import text
        row = db.execute(
            text(
                "SELECT uuid, title, author, original_filename, status, file_extension, "
                "file_size_bytes, total_chunks, indexed_chunks, minio_object_key "
                "FROM books WHERE uuid = :bid"
            ),
            {"bid": book_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    return {
        "uuid": row[0], "title": row[1], "author": row[2],
        "original_filename": row[3], "status": row[4], "file_extension": row[5],
        "file_size_bytes": row[6], "total_chunks": row[7], "indexed_chunks": row[8],
        "minio_object_key": row[9],
    }
