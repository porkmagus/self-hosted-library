"""Idempotent asynchronous cleanup for soft-deleted books."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from api.config import settings
from api.models import Book, BookStatus, IngestionJob, IngestionState
from api.services.image_svc import parse_persisted_image_manifest
from api.services.ingestion_jobs import finalize_cancel
from api.services.object_store import get_object_store
from api.services.qdrant_svc import get_qdrant_client


def cleanup_stale_upload_reservations(
    session: Session,
    *,
    store: Any | None = None,
    stale_seconds: int = 3600,
    limit: int = 100,
) -> int:
    """Resolve DB/S3 orphans left by a crash between upload and acceptance TX2."""
    now = session.execute(select(func.now())).scalar_one()

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=stale_seconds)
    reservations = list(
        session.execute(
            select(Book)
            .where(
                Book.status == BookStatus.UPLOADING,
                Book.created_at < stale_before,
            )
            .order_by(Book.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    object_store = store or get_object_store()
    for book in reservations:
        if book.source_object_key and object_store.exists(book.source_object_key):
            object_store.delete(book.source_object_key)
        book.status = BookStatus.FAILED
        book.error_message = "Upload reservation expired before durable job acceptance"
    session.flush()
    return len(reservations)


def _delete_vectors(book_uuid: str) -> int:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    selector = Filter(
        must=[FieldCondition(key="book_id", match=MatchValue(value=book_uuid))]
    )
    client = get_qdrant_client()
    deleted = 0
    for collection in (settings.QDRANT_COLLECTION, settings.IMAGE_COLLECTION):
        result = client.delete(
            collection_name=collection,
            points_selector=selector,
            wait=True,
        )
        deleted += int(getattr(result, "deleted", 0) or 0)
    return deleted


def cleanup_deleted_book(
    session: Session,
    book_uuid: str,
    *,
    store: Any | None = None,
    delete_vectors: Callable[[str], int] = _delete_vectors,
) -> dict[str, int | str]:
    """Delete external state only after the DB cancellation fence is durable."""
    book = session.execute(
        select(Book).where(Book.uuid == book_uuid)
    ).scalar_one_or_none()
    if book is None:
        return {"book_id": book_uuid, "status": "already_removed", "objects_deleted": 0}
    if book.deleted_at is None:
        raise RuntimeError("Cleanup refused because the book is not soft-deleted")

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:book_uuid))"),
            {"book_uuid": book_uuid},
        )
    book = session.execute(
        select(Book).where(Book.id == book.id).with_for_update()
    ).scalar_one()
    if book.cleanup_completed_at is not None:
        return {
            "book_id": book_uuid,
            "status": "already_cleaned",
            "objects_deleted": 0,
            "vectors_deleted": 0,
        }
    jobs = list(
        session.execute(
            select(IngestionJob).where(IngestionJob.book_id == book.id)
        ).scalars()
    )
    object_store = store or get_object_store()
    keys = {book.source_object_key} if book.source_object_key else set()
    keys.update(object_store.iter_keys(f"books/{book.uuid}/generations/"))
    for job in jobs:
        keys.update(object_store.iter_keys(f"jobs/{job.uuid}/"))
    manifest_keys = {
        key
        for job in jobs
        for key in (
            job.extracted_text_key,
            job.chunk_manifest_key,
            job.image_manifest_key,
        )
        if key
    }
    keys.update(manifest_keys)
    for job in jobs:
        if job.image_manifest_key and object_store.exists(job.image_manifest_key):
            manifest_payload = bytearray()
            for chunk in object_store.iter_bytes(job.image_manifest_key):
                manifest_payload.extend(chunk)
                if len(manifest_payload) > 4_000_000:
                    raise RuntimeError("Image manifest exceeds cleanup limit")
            images = parse_persisted_image_manifest(
                bytes(manifest_payload), book_id=book.uuid, generation=job.generation
            )
            keys.update(
                str(image["object_key"])
                for image in images
                if image.get("object_key")
            )
    objects_deleted = 0
    for key in keys - manifest_keys:
        if object_store.exists(key):
            object_store.delete(key)
            objects_deleted += 1
    for key in manifest_keys:
        if object_store.exists(key):
            object_store.delete(key)
            objects_deleted += 1
    deleted_vectors = delete_vectors(book_uuid)
    for job in jobs:
        if job.state == IngestionState.CANCEL_REQUESTED:
            finalize_cancel(session, job.uuid)
    book.cleanup_completed_at = session.execute(select(func.now())).scalar_one()
    session.flush()
    return {
        "book_id": book_uuid,
        "status": "cleaned",
        "objects_deleted": objects_deleted,
        "vectors_deleted": deleted_vectors,
    }
