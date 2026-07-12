"""Books router — list, detail, and SSE progress streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.config import settings
from api.models import Book, get_db
from api.tasks.celery_app import ingest_batch_task

router = APIRouter()


@router.get("/ingest/status")
async def get_ingest_status() -> dict[str, Any]:
    """Aggregate ingestion progress from the books table — real numbers, not Celery task state."""
    db = next(iter(get_db()))
    try:
        from sqlalchemy import func

        rows = db.query(Book.status, func.count(Book.id)).group_by(Book.status).all()
        counts = {row[0].value if row[0] else "none": row[1] for row in rows}

        total = sum(counts.values())
        indexed = counts.get("indexed", 0)
        failed = counts.get("failed", 0)
        in_progress = total - indexed - failed

        pct = (indexed / total * 100) if total > 0 else 0

        # Qdrant stats
        from api.services.qdrant_svc import get_collection_stats

        try:
            qdrant_stats = get_collection_stats()
        except Exception:
            qdrant_stats = {}

        return {
            "total_books": total,
            "indexed": indexed,
            "in_progress": in_progress,
            "failed": failed,
            "progress_pct": round(pct, 1),
            "qdrant_chunks": qdrant_stats.get("points_count", 0),
        }
    finally:
        db.close()


@router.get("/books")
async def list_books(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
) -> dict[str, Any]:
    """List all books in the library with optional filtering."""
    db = next(iter(get_db()))
    try:
        q = db.query(Book)

        if status:
            q = q.filter(Book.status == status)
        if search:
            search_term = f"%{search}%"
            q = q.filter(
                (Book.title.ilike(search_term))
                | (Book.author.ilike(search_term))
                | (Book.original_filename.ilike(search_term))
            )

        total = q.count()
        books = q.order_by(Book.created_at.desc()).offset(offset).limit(limit).all()

        return {
            "books": [
                {
                    "id": b.id,
                    "uuid": b.uuid,
                    "title": b.title,
                    "author": b.author,
                    "original_filename": b.original_filename,
                    "file_extension": b.file_extension,
                    "file_size_bytes": b.file_size_bytes,
                    "status": b.status.value if b.status else None,
                    "total_chunks": b.total_chunks,
                    "indexed_chunks": b.indexed_chunks,
                    "created_at": b.created_at.isoformat() if b.created_at else None,
                    "error_message": b.error_message,
                }
                for b in books
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.get("/books/{book_id}")
async def get_book(book_id: str) -> dict[str, Any]:
    """Get details for a specific book by UUID."""
    db = next(iter(get_db()))
    try:
        book = db.query(Book).filter(Book.uuid == book_id).first()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        return {
            "id": book.id,
            "uuid": book.uuid,
            "title": book.title,
            "author": book.author,
            "original_filename": book.original_filename,
            "sanitized_filename": book.sanitized_filename,
            "file_extension": book.file_extension,
            "file_size_bytes": book.file_size_bytes,
            "file_hash": book.file_hash,
            "minio_object_key": book.minio_object_key,
            "markdown_path": book.markdown_path,
            "status": book.status.value if book.status else None,
            "total_chunks": book.total_chunks,
            "indexed_chunks": book.indexed_chunks,
            "error_message": book.error_message,
            "created_at": book.created_at.isoformat() if book.created_at else None,
            "updated_at": book.updated_at.isoformat() if book.updated_at else None,
        }
    finally:
        db.close()


@router.post("/ingest/local")
async def ingest_local_books(
    paths: list[str] | None = Query(None),
    scan_inbox: bool = True,
) -> dict[str, Any]:
    """Trigger ingestion of local books from the data directory.

    If scan_inbox is true, scans the configured data directory's inbox.
    If paths is provided, only ingests those specific files.
    """
    data_dir = Path(settings.DATA_DIR)

    if paths:
        book_paths = [str(data_dir / p) for p in paths]
    elif scan_inbox:
        inbox = data_dir / "inbox"
        if not inbox.exists():
            raise HTTPException(
                status_code=404, detail=f"Inbox directory not found: {inbox}"
            )

        supported_exts = {
            ".pdf",
            ".epub",
            ".mobi",
            ".docx",
            ".rtf",
            ".doc",
            ".txt",
            ".md",
            ".htm",
            ".html",
            ".PDF",
            ".EPUB",
            ".DOCX",
            ".DOC",
            ".TXT",
            ".MD",
        }
        book_paths = [
            str(f)
            for f in inbox.rglob("*")
            if f.is_file() and f.suffix.lower() in supported_exts
        ]

    if not book_paths:
        return {"message": "No books to ingest", "count": 0}

    # Queue batch ingestion
    task = ingest_batch_task.delay(book_paths)

    return {
        "task_id": task.id,
        "book_count": len(book_paths),
        "status": "queued",
    }


@router.get("/ingest/{task_id}/progress")
async def get_ingest_progress(task_id: str) -> dict[str, Any]:
    """Get ingestion progress — backed by the DB, not Celery task state."""
    # Always return real aggregate numbers
    db = next(iter(get_db()))
    try:
        from sqlalchemy import func

        rows = db.query(Book.status, func.count(Book.id)).group_by(Book.status).all()
        counts = {row[0].value if row[0] else "none": row[1] for row in rows}

        total = sum(counts.values())
        indexed = counts.get("indexed", 0)
        failed = counts.get("failed", 0)
        in_progress = total - indexed - failed

        pct = (indexed / total * 100) if total > 0 else 0

        from api.services.qdrant_svc import get_collection_stats

        try:
            qdrant_stats = get_collection_stats()
        except Exception:
            qdrant_stats = {}

        return {
            "task_id": task_id,
            "status": "processing"
            if in_progress
            else (
                "completed" if total > 0 and failed == 0 else "completed_with_errors"
            ),
            "progress": round(pct, 1),
            "progress_pct": round(pct, 1),
            "total_books": total,
            "indexed": indexed,
            "in_progress": in_progress,
            "failed": failed,
            "qdrant_chunks": qdrant_stats.get("points_count", 0),
        }
    finally:
        db.close()


@router.get("/ingest/{task_id}/sse")
async def stream_ingest_progress(task_id: str) -> StreamingResponse:
    """Server-Sent Events stream for real-time ingestion progress — backed by DB."""

    async def event_stream() -> AsyncIterator[str]:
        last_indexed = None
        last_failed = None

        while True:
            db = next(iter(get_db()))
            try:
                from sqlalchemy import func

                rows = (
                    db.query(Book.status, func.count(Book.id))
                    .group_by(Book.status)
                    .all()
                )
                counts = {row[0].value if row[0] else "none": row[1] for row in rows}

                total = sum(counts.values())
                indexed = counts.get("indexed", 0)
                failed = counts.get("failed", 0)
                in_progress = total - indexed - failed

                pct = (indexed / total * 100) if total > 0 else 0

                from api.services.qdrant_svc import get_collection_stats

                try:
                    qdrant_stats = get_collection_stats()
                except Exception:
                    qdrant_stats = {}

                chunks = qdrant_stats.get("points_count", 0)

                # Only emit if something changed
                if indexed != last_indexed or failed != last_failed:
                    last_indexed = indexed
                    last_failed = failed

                    data = {
                        "task_id": task_id,
                        "status": "processing"
                        if in_progress
                        else (
                            "completed"
                            if total > 0 and failed == 0
                            else "completed_with_errors"
                        ),
                        "progress": round(pct, 1),
                        "progress_pct": round(pct, 1),
                        "total_books": total,
                        "indexed": indexed,
                        "in_progress": in_progress,
                        "failed": failed,
                        "qdrant_chunks": chunks,
                    }
                    yield f"data: {json.dumps(data)}\n\n"

                    if in_progress == 0 and total > 0:
                        yield f"event: done\ndata: {json.dumps(data)}\n\n"
                        break
            finally:
                db.close()

            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
