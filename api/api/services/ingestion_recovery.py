"""Explicit, non-destructive recovery of terminal ingestion jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.models import (
    Book,
    BookStatus,
    IngestionJob,
    IngestionOutbox,
    IngestionStage,
    IngestionState,
    OutboxState,
)
from api.services.ingestion_outbox import create_dispatch_event

_STAGE_BOOK_STATUS = {
    IngestionStage.SOURCE_READY: BookStatus.PENDING,
    IngestionStage.EXTRACTING: BookStatus.EXTRACTING,
    IngestionStage.CHUNKING: BookStatus.CHUNKING,
    IngestionStage.EMBEDDING: BookStatus.EMBEDDING,
    IngestionStage.IMAGES: BookStatus.EMBEDDING,
    IngestionStage.FINALIZING: BookStatus.EMBEDDING,
}


def requeue_failed_jobs(
    session: Session,
    job_uuids: list[str],
    *,
    max_attempts: int = 8,
    now: datetime | None = None,
) -> list[str]:
    """Requeue explicit current-generation failed jobs without discarding progress."""
    requested = list(dict.fromkeys(job_uuids))
    if not requested:
        return []
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    current = now or datetime.now(timezone.utc)

    jobs = list(
        session.execute(
            select(IngestionJob)
            .where(IngestionJob.uuid.in_(requested))
            .order_by(IngestionJob.id)
            .with_for_update()
        ).scalars()
    )
    if {job.uuid for job in jobs} != set(requested):
        raise ValueError("One or more requested ingestion jobs do not exist")

    requeued: list[str] = []
    for job in jobs:
        book = session.execute(
            select(Book).where(Book.id == job.book_id).with_for_update()
        ).scalar_one()
        if job.state != IngestionState.FAILED:
            raise ValueError(f"Job {job.uuid} is not failed")
        if book.status != BookStatus.FAILED:
            raise ValueError(f"Book {book.uuid} is not failed")
        if book.processing_generation != job.generation:
            raise ValueError(f"Job {job.uuid} is not the current generation")
        if book.indexed_generation is not None or book.indexed_job_uuid is not None:
            raise ValueError(f"Book {book.uuid} already has an indexed generation")

        job.state = IngestionState.PENDING
        job.attempt_count = 0
        job.max_attempts = max_attempts
        job.retry_at = None
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        job.last_error_class = None
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.celery_task_id = None
        book.status = _STAGE_BOOK_STATUS[job.stage]
        book.error_message = None

        event = session.execute(
            select(IngestionOutbox)
            .where(
                IngestionOutbox.job_uuid == job.uuid,
                IngestionOutbox.event_type == "dispatch_ingestion",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if event is None:
            event = create_dispatch_event(session, job.uuid)
        else:
            event.state = OutboxState.PENDING
            event.available_at = current
            event.claimed_at = None
            event.published_at = None
            event.publish_claim_token = None
            event.last_recovered_claim_epoch = None
            event.last_error = None
        requeued.append(job.uuid)

    session.flush()
    return requeued


def recover_reconciliation_events(
    session: Session, *, limit: int = 100, stale_seconds: int = 86_400
) -> int:
    """Reopen lifecycle events only after a published delivery is stale."""
    if stale_seconds < 0:
        raise ValueError("stale_seconds must be non-negative")
    now = session.execute(select(func.now())).scalar_one()
    stale_before = now - timedelta(seconds=stale_seconds)
    events = list(
        session.execute(
            select(IngestionOutbox)
            .where(
                IngestionOutbox.state == OutboxState.PUBLISHED,
                IngestionOutbox.lifecycle_recovery_count < 1,
                or_(
                    IngestionOutbox.published_at.is_(None),
                    IngestionOutbox.published_at <= stale_before,
                ),
                or_(
                    (
                        (IngestionOutbox.event_type == "activate_generation")
                        & IngestionOutbox.job_uuid.in_(
                            select(IngestionJob.uuid)
                            .join(Book, Book.id == IngestionJob.book_id)
                            .where(
                                IngestionJob.state == IngestionState.SUCCEEDED,
                                Book.deleted_at.is_(None),
                                or_(
                                    Book.indexed_generation.is_(None),
                                    Book.indexed_generation != IngestionJob.generation,
                                    (
                                        Book.activation_cleanup_pending.is_(True)
                                        & (Book.indexed_job_uuid == IngestionJob.uuid)
                                    ),
                                ),
                            )
                        )
                    ),
                    (
                        (IngestionOutbox.event_type == "cleanup_book")
                        & IngestionOutbox.aggregate_uuid.in_(
                            select(Book.uuid)
                            .where(
                                Book.deleted_at.is_not(None),
                                Book.cleanup_completed_at.is_(None),
                            )
                        )
                    ),
                ),
            )
            .order_by(IngestionOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    for event in events:
        event.state = OutboxState.PENDING
        event.available_at = now
        event.claimed_at = None
        event.published_at = None
        event.publish_claim_token = None
        event.lifecycle_recovery_count += 1
        event.last_error = None
    session.flush()
    return len(events)
