"""Books router — list, detail, delete, and SSE progress streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func

from api.config import settings
from api.models import Book, BookStatus, get_db_session
from api.services.path_svc import resolve_under

router = APIRouter()
logger = logging.getLogger(__name__)


def _summarize_counts(
    counts: Mapping[str, int], qdrant_chunks: int
) -> dict[str, Any]:
    total = sum(counts.values())
    indexed = counts.get("indexed", 0)
    failed = counts.get("failed", 0)
    in_progress = total - indexed - failed
    progress_pct = round(indexed / total * 100, 1) if total else 0.0
    if total == 0:
        status = "idle"
    elif in_progress:
        status = "processing"
    elif failed:
        status = "completed_with_errors"
    else:
        status = "completed"
    return {
        "total_books": total,
        "indexed": indexed,
        "in_progress": in_progress,
        "failed": failed,
        "progress_pct": progress_pct,
        "qdrant_chunks": qdrant_chunks,
        "status": status,
    }


def _get_progress_snapshot() -> dict[str, Any]:
    with get_db_session() as db:
        rows = db.query(Book.status, func.count(Book.id)).group_by(Book.status).all()
        counts = {row[0].value if row[0] else "none": row[1] for row in rows}

    try:
        from api.services.qdrant_svc import get_collection_stats

        chunks = int(get_collection_stats().get("points_count", 0) or 0)
    except Exception as exc:
        logger.warning("Unable to read Qdrant collection stats: %s", exc)
        chunks = 0
    return _summarize_counts(counts, chunks)


@router.get("/ingest/status")
def get_ingest_status() -> dict[str, Any]:
    """Aggregate ingestion progress from the books table — real numbers, not Celery task state."""
    return _get_progress_snapshot()


@router.get("/books")
def list_books(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
) -> dict[str, Any]:
    """List all books in the library with optional filtering."""
    with get_db_session() as db:
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
            "has_more": (offset + limit) < total,
        }


@router.get("/books/{book_id}")
def get_book(book_id: str) -> dict[str, Any]:
    """Get details for a specific book by UUID."""
    with get_db_session() as db:
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


@router.delete("/books/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    """Delete a book: remove from Qdrant, MinIO, filesystem, and database."""
    from api.config import settings as cfg
    from api.services.minio_svc import get_minio_client
    from api.services.qdrant_svc import get_qdrant_client

    with get_db_session() as db:
        book = db.query(Book).filter(Book.uuid == book_id).first()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        deleted_chunks = 0
        deleted_minio = False
        deleted_file = False
        deleted_markdown = False

        # 1. Delete from Qdrant
        try:
            qdrant = get_qdrant_client()
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            result = qdrant.delete(
                collection_name=cfg.QDRANT_COLLECTION,
                points_selector=Filter(
                    must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))]
                ),
            )
            deleted_chunks = getattr(result, "deleted", 0) or 0
            logger.info("Deleted %d Qdrant points for book %s", deleted_chunks, book_id)
        except Exception as exc:
            logger.warning("Failed to delete Qdrant points for %s: %s", book_id, exc)

        # 2. Delete from MinIO
        if book.minio_object_key:
            try:
                minio = get_minio_client()
                minio.remove_object(cfg.MINIO_BUCKET, book.minio_object_key)
                deleted_minio = True
                logger.info("Deleted MinIO object %s for book %s", book.minio_object_key, book_id)
            except Exception as exc:
                logger.warning("Failed to delete MinIO object for %s: %s", book_id, exc)

        # 3. Delete markdown file
        if book.markdown_path:
            md_path = Path(book.markdown_path)
            if md_path.exists():
                try:
                    md_path.unlink()
                    deleted_markdown = True
                except Exception as exc:
                    logger.warning("Failed to delete markdown %s: %s", md_path, exc)

        # 4. Delete from database
        db.delete(book)
        db.commit()

        return {
            "deleted": True,
            "book_id": book_id,
            "qdrant_chunks": deleted_chunks,
            "minio_deleted": deleted_minio,
            "file_deleted": deleted_file,
            "markdown_deleted": deleted_markdown,
        }


@router.get("/ingest/{task_id}/progress")
def get_ingest_progress(task_id: str) -> dict[str, Any]:
    """Get aggregate ingestion progress from durable database state."""
    snapshot = _get_progress_snapshot()
    return {
        "task_id": task_id,
        "progress": snapshot["progress_pct"],
        **snapshot,
    }


@router.get("/ingest/{task_id}/books")
def get_ingest_books(task_id: str) -> dict[str, Any]:
    """Get individual book ingestion status for live progress tracking."""
    with get_db_session() as db:
        books = db.query(Book).filter(
            Book.status.in_([
                BookStatus.EXTRACTING, BookStatus.CHUNKING,
                BookStatus.EMBEDDING, BookStatus.PENDING,
            ])
        ).all()
        active = [
            {
                "uuid": b.uuid,
                "title": b.title,
                "status": b.status.value if b.status else "unknown",
                "total_chunks": b.total_chunks,
                "indexed_chunks": b.indexed_chunks,
                "progress": round(b.indexed_chunks / b.total_chunks * 100, 1) if b.total_chunks else 0,
            }
            for b in books
        ]

        failed = db.query(Book).filter(Book.status == BookStatus.FAILED).all()
        failed_list = [
            {
                "uuid": b.uuid,
                "title": b.title,
                "error_message": b.error_message,
            }
            for b in failed
        ]

        return {
            "task_id": task_id,
            "active": active,
            "failed": failed_list,
            "active_count": len(active),
            "failed_count": len(failed_list),
        }


@router.get("/ingest/{task_id}/sse")
async def stream_ingest_progress(task_id: str) -> StreamingResponse:
    """Server-Sent Events stream for real-time ingestion progress — backed by DB."""

    async def event_stream() -> AsyncIterator[str]:
        last_fingerprint: tuple[Any, ...] | None = None

        while True:
            snapshot = await asyncio.to_thread(_get_progress_snapshot)
            data = {
                "task_id": task_id,
                "progress": snapshot["progress_pct"],
                **snapshot,
            }
            fingerprint = (
                data["indexed"],
                data["failed"],
                data["in_progress"],
                data["qdrant_chunks"],
                data["status"],
            )
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                yield f"data: {json.dumps(data)}\n\n"

                if data["status"] in {"completed", "completed_with_errors"}:
                    yield f"event: done\ndata: {json.dumps(data)}\n\n"
                    break

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


@router.post("/ingest/local")
def ingest_local_books(
    paths: list[str] | None = Query(None),
    scan_inbox: bool = True,
) -> dict[str, Any]:
    """Trigger ingestion of local books from the data directory.

    If scan_inbox is true, scans the configured data directory's inbox.
    If paths is provided, only ingests those specific files.
    """
    from api.tasks.celery_app import ingest_batch_task

    data_dir = Path(settings.DATA_DIR)

    if paths:
        try:
            book_paths = [str(resolve_under(data_dir, p)) for p in paths]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif scan_inbox:
        inbox = data_dir / "inbox"
        if not inbox.exists():
            raise HTTPException(
                status_code=404, detail=f"Inbox directory not found: {inbox}"
            )

        supported_exts = {
            ".pdf", ".epub", ".docx", ".doc",
            ".txt", ".md", ".htm", ".html",
        }
        book_paths = [
            str(f)
            for f in inbox.rglob("*")
            if f.is_file() and f.suffix.lower() in supported_exts
        ]

    if not book_paths:
        return {"message": "No books to ingest", "count": 0}

    task = ingest_batch_task.delay(book_paths)

    return {
        "task_id": task.id,
        "book_count": len(book_paths),
        "status": "queued",
    }
