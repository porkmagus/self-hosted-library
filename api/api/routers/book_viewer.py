"""Book viewer router — serve PDFs and markdown from MinIO."""

from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from api.config import settings
from api.services.minio_svc import get_minio_client

router = APIRouter()


@router.get("/book/{book_id}/pdf")
async def get_book_pdf(book_id: str) -> StreamingResponse:
    """Stream a book's original PDF from MinIO."""
    conn = psycopg2.connect(dsn=settings.DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT minio_object_key, title FROM books WHERE uuid = %s",
        (book_id,),
    )
    book = cur.fetchone()
    cur.close()
    conn.close()

    if not book or not book.get("minio_object_key"):
        raise HTTPException(
            status_code=404, detail="Book not found or no PDF available"
        )

    minio = get_minio_client()
    try:
        stat = minio.stat_object(settings.MINIO_BUCKET, book["minio_object_key"])
    except Exception:
        raise HTTPException(
            status_code=404, detail="File not found in storage"
        ) from None

    def stream() -> Any:
        response = minio.get_object(settings.MINIO_BUCKET, book["minio_object_key"])
        try:
            yield from response.stream(8192)
        finally:
            response.close()

    return StreamingResponse(
        stream(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{book.get("title", "book")}.pdf"',
            "Content-Length": str(stat.size),
        },
    )


@router.get("/book/{book_id}/markdown")
async def get_book_markdown(book_id: str) -> Response:
    """Return parsed markdown for a book if available."""
    conn = psycopg2.connect(dsn=settings.DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT markdown_path, title FROM books WHERE uuid = %s",
        (book_id,),
    )
    book = cur.fetchone()
    cur.close()
    conn.close()

    if not book or not book.get("markdown_path"):
        raise HTTPException(
            status_code=404, detail="No markdown available for this book"
        )

    md_path = book["markdown_path"]
    try:
        with open(md_path) as f:
            content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Markdown file not found") from None

    return Response(content, media_type="text/markdown")


@router.get("/book/{book_id}")
async def get_book_info(book_id: str) -> dict[str, Any]:
    """Get book metadata."""
    conn = psycopg2.connect(dsn=settings.DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT uuid, title, author, original_filename, status, file_extension, "
        "file_size_bytes, total_chunks, indexed_chunks, minio_object_key "
        "FROM books WHERE uuid = %s",
        (book_id,),
    )
    book = cur.fetchone()
    cur.close()
    conn.close()

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    return dict(book)
