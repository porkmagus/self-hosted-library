"""Celery boundaries for fenced ingestion, activation, and outbox dispatch."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from celery import Celery
from sqlalchemy import func, or_, select, text

from api.config import settings
from api.models import (
    Book,
    BookStatus,
    IngestionJob,
    IngestionOutbox,
    IngestionState,
    OutboxState,
    get_db_session,
)
from api.services.image_svc import retire_other_image_activations
from api.services.ingestion_activation import reconcile_generation_activation
from api.services.ingestion_cleanup import (
    cleanup_deleted_book,
    cleanup_stale_upload_reservations,
)
from api.services.ingestion_jobs import (
    JobLease,
    LeaseUnavailable,
    StaleLease,
    fail_job,
    release_for_retry,
)
from api.services.ingestion_outbox import (
    claim_dispatch_events,
    create_dispatch_event,
    mark_publish_failed,
    mark_published,
)
from api.services.ingestion_pipeline import InvalidDocumentError, run_ingestion_pipeline
from api.services.ingestion_recovery import recover_reconciliation_events
from api.services.ingestion_submit import submit_uploaded_book
from api.services.object_store import get_object_store
from api.services.qdrant_svc import retire_other_activations

logger = logging.getLogger(__name__)
MAX_INGEST_RETRIES = 8


def make_celery() -> Celery:
    app = Celery("self_hosted_library")
    app.conf.update(
        broker_url=settings.REDIS_URL,
        result_backend=settings.REDIS_URL,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=None,
        task_soft_time_limit=None,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_queues={
            "celery": {"routing_key": "celery"},
            "high": {"routing_key": "high"},
        },
        task_default_queue="celery",
        task_default_routing_key="celery",
        task_routes = {
            "ingest.activate": {"queue": "high"},
            "ingest.cleanup_book": {"queue": "high"},
            "ingest.dispatch_outbox": {"queue": "high"},
        },
        broker_transport_options={"visibility_timeout": 12 * 60 * 60},
        beat_schedule={
            "publish-ingestion-outbox": {
                "task": "ingest.dispatch_outbox",
                "schedule": 5.0,
            },
            "recover-durable-ingestion-jobs": {
                "task": "ingest.recover",
                "schedule": 30.0,
            },
            "cleanup-stale-upload-reservations": {
                "task": "ingest.cleanup_stale_uploads",
                "schedule": 600.0,
            },
        },
    )
    return app


celery_app = make_celery()


def _owner(task: Any) -> str:
    task_id = getattr(task.request, "id", None) or str(uuid.uuid4())
    hostname = getattr(task.request, "hostname", None) or "worker"
    return f"{hostname}:{task_id}"


def _owned_lease(job: IngestionJob, owner: str) -> JobLease | None:
    if (
        job.state != IngestionState.RUNNING
        or job.lease_owner != owner
        or not job.lease_token
        or not job.lease_expires_at
    ):
        return None
    expires = job.lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return JobLease(
        job_uuid=job.uuid,
        generation=job.generation,
        owner=owner,
        token=job.lease_token,
        claim_epoch=job.claim_epoch,
        expires_at=expires,
        next_chunk_index=job.next_chunk_index,
        indexed_chunks=job.indexed_chunks,
    )


def _transition_failure(
    job_uuid: str,
    owner: str,
    exc: Exception,
    *,
    terminal: bool,
    error_code: str,
) -> bool:
    with get_db_session() as session:
        job = session.execute(
            select(IngestionJob).where(IngestionJob.uuid == job_uuid)
        ).scalar_one_or_none()
        if job is None:
            return False
        lease = _owned_lease(job, owner)
        if lease is None:
            return False
        try:
            if terminal or job.attempt_count >= job.max_attempts:
                fail_job(
                    session,
                    lease,
                    error_code=error_code,
                    error_message=str(exc),
                    error_class=type(exc).__name__,
                )
                book = session.get(Book, job.book_id)
                if book is not None:
                    book.status = BookStatus.FAILED
                    book.error_message = str(exc)[:2000]
            else:
                now = session.execute(select(func.now())).scalar_one()
                retries = max(0, job.attempt_count - 1)
                release_for_retry(
                    session,
                    lease,
                    error_code=error_code,
                    error_message=str(exc),
                    error_class=type(exc).__name__,
                    retry_at=now + timedelta(seconds=min(300, 5 * (2**retries))),
                )
            session.commit()
            return True
        except StaleLease:
            session.rollback()
            return False


@celery_app.task(bind=True, name="ingest.job", max_retries=MAX_INGEST_RETRIES)
def ingest_job_task(self: Any, job_uuid: str) -> dict[str, Any]:
    """Execute only a durable job UUID; PostgreSQL owns all resumable state."""
    owner = _owner(self)
    try:
        return run_ingestion_pipeline(job_uuid, owner)
    except LeaseUnavailable as exc:
        logger.info("Skipping duplicate delivery for %s: %s", job_uuid, exc)
        return {"job_uuid": job_uuid, "status": "leased_or_terminal"}
    except InvalidDocumentError as exc:
        _transition_failure(
            job_uuid,
            owner,
            exc,
            terminal=True,
            error_code="invalid_document",
        )
        return {"job_uuid": job_uuid, "status": "failed", "error": str(exc)}
    except Exception as exc:
        logger.exception("Transient ingestion failure for %s", job_uuid)
        transitioned = _transition_failure(
            job_uuid,
            owner,
            exc,
            terminal=False,
            error_code="transient_dependency_failure",
        )
        if not transitioned:
            return {"job_uuid": job_uuid, "status": "stale_worker"}
        retries = int(getattr(self.request, "retries", 0))
        if retries >= MAX_INGEST_RETRIES:
            raise
        raise self.retry(exc=exc, countdown=min(300, 5 * (2**retries))) from exc


@celery_app.task(bind=True, name="ingest.activate", max_retries=MAX_INGEST_RETRIES)
def activate_generation_task(self: Any, job_uuid: str) -> dict[str, Any]:
    try:
        with get_db_session() as session:
            result = reconcile_generation_activation(session, job_uuid)
            session.commit()
        book_id = str(result["book_id"])
        activation_token = str(result["activation_token"])
        with get_db_session() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:book_uuid))"),
                    {"book_uuid": book_id},
                )
            book = session.execute(
                select(Book).where(Book.uuid == book_id).with_for_update()
            ).scalar_one()
            if book.indexed_activation_token == activation_token:
                retire_other_activations(book_id, activation_token)
                retire_other_image_activations(book_id, activation_token)
                book.activation_cleanup_pending = False
            session.commit()
        return result
    except Exception as exc:
        logger.exception("Generation activation failed for %s", job_uuid)
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(300, 5 * (2**retries))) from exc


@celery_app.task(bind=True, name="ingest.cleanup_book", max_retries=MAX_INGEST_RETRIES)
def cleanup_book_task(self: Any, book_uuid: str) -> dict[str, Any]:
    try:
        with get_db_session() as session:
            result = cleanup_deleted_book(session, book_uuid)
            session.commit()
            return result
    except Exception as exc:
        logger.exception("Book cleanup failed for %s", book_uuid)
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(300, 5 * (2**retries))) from exc


@celery_app.task(name="ingest.cleanup_stale_uploads")
def cleanup_stale_uploads() -> dict[str, int]:
    with get_db_session() as session:
        cleaned = cleanup_stale_upload_reservations(session)
        session.commit()
    return {"cleaned": cleaned}


@celery_app.task(name="ingest.file")
def ingest_book_task(book_path: str) -> dict[str, Any]:
    """Import a server-local file through the durable object-backed boundary."""
    path = Path(book_path)
    if not path.is_file():
        raise FileNotFoundError(f"Book not found: {book_path}")
    with path.open("rb") as source, get_db_session() as session:
        result = submit_uploaded_book(
            session,
            filename=path.name,
            content_type="application/octet-stream",
            stream=source,
            store=get_object_store(),
        )
    return {
        "book_id": result.book_uuid,
        "job_uuid": result.job_uuid,
        "task_id": result.task_id,
        "status": "queued",
    }


@celery_app.task(bind=True, name="ingest.batch")
def ingest_batch_task(self: Any, book_paths: list[str]) -> dict[str, Any]:
    total = len(book_paths)
    for index, path in enumerate(book_paths):
        ingest_book_task.delay(path)
        self.update_state(
            state="STARTED",
            meta={
                "status": "batch_queued",
                "progress": int((index + 1) / total * 100),
                "current": index + 1,
                "total": total,
            },
        )
    return {"total": total, "status": "all_queued"}


@celery_app.task(name="ingest.dispatch_outbox")
def dispatch_outbox(limit: int = 100) -> dict[str, int]:
    """Publish claimed outbox rows; uncertain publishes are safely redelivered."""
    with get_db_session() as session:
        events = claim_dispatch_events(session, limit=limit)
        session.commit()

    published = 0
    failed = 0
    for event in events:
        try:
            if event.event_type == "dispatch_ingestion":
                if event.job_uuid is None:
                    raise ValueError("Dispatch event has no job UUID")
                task_id = str(ingest_job_task.delay(event.job_uuid).id)
            elif event.event_type == "activate_generation":
                if event.job_uuid is None:
                    raise ValueError("Activation event has no job UUID")
                task_id = str(activate_generation_task.delay(event.job_uuid).id)
            elif event.event_type == "cleanup_book":
                task_id = str(cleanup_book_task.delay(event.aggregate_uuid).id)
            else:
                raise ValueError(f"Unsupported outbox event: {event.event_type}")
            with get_db_session() as session:
                mark_published(session, event.uuid, task_id=task_id)
                session.commit()
            published += 1
        except Exception as exc:
            logger.exception("Outbox publish failed for %s", event.uuid)
            with get_db_session() as session:
                mark_publish_failed(
                    session,
                    event.uuid,
                    error=str(exc),
                    retry_seconds=10,
                )
                session.commit()
            failed += 1
    return {"published": published, "failed": failed}


@celery_app.task(name="ingest.recover")
def recover_durable_jobs(limit: int = 100) -> dict[str, int]:
    """Reset the reusable dispatch event for eligible or expired jobs."""
    reset = 0
    exhausted = 0
    with get_db_session() as session:
        now = session.execute(select(func.now())).scalar_one()
        exhausted_ids = list(
            session.execute(
                select(IngestionJob.uuid)
                .where(
                    IngestionJob.state == IngestionState.RUNNING,
                    IngestionJob.attempt_count >= IngestionJob.max_attempts,
                    IngestionJob.lease_expires_at < now,
                )
                .limit(limit)
            ).scalars()
        )
        for exhausted_uuid in exhausted_ids:
            initial = session.execute(
                select(IngestionJob).where(IngestionJob.uuid == exhausted_uuid)
            ).scalar_one()
            book = session.execute(
                select(Book).where(Book.id == initial.book_id).with_for_update()
            ).scalar_one()
            job = session.execute(
                select(IngestionJob)
                .where(IngestionJob.uuid == exhausted_uuid)
                .with_for_update()
            ).scalar_one()
            if (
                job.state == IngestionState.RUNNING
                and job.attempt_count >= job.max_attempts
                and job.lease_expires_at is not None
                and job.lease_expires_at < now
            ):
                job.state = IngestionState.FAILED
                job.finished_at = now
                job.error_code = "worker_lost_retry_exhausted"
                job.error_message = "Worker lease expired on the final allowed attempt"
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                if (
                    book.deleted_at is None
                    and book.processing_generation == job.generation
                ):
                    book.status = BookStatus.FAILED
                    book.error_message = job.error_message
                exhausted += 1
        jobs = list(
            session.execute(
                select(IngestionJob)
                .where(
                    IngestionJob.attempt_count < IngestionJob.max_attempts,
                    or_(
                        IngestionJob.state == IngestionState.PENDING,
                        (
                            (IngestionJob.state == IngestionState.RETRY_WAIT)
                            & (IngestionJob.retry_at <= now)
                        ),
                        (
                            (IngestionJob.state == IngestionState.RUNNING)
                            & (IngestionJob.lease_expires_at < now)
                        ),
                    ),
                )
                .order_by(IngestionJob.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        for job in jobs:
            event = session.execute(
                select(IngestionOutbox).where(
                    IngestionOutbox.job_uuid == job.uuid,
                    IngestionOutbox.event_type == "dispatch_ingestion",
                )
            ).scalar_one_or_none()
            if event is None:
                create_dispatch_event(session, job.uuid)
                reset += 1
            elif event.state == OutboxState.PUBLISHED:
                event.state = OutboxState.PENDING
                event.available_at = now
                event.claimed_at = None
                event.last_error = None
                reset += 1
        reset += recover_reconciliation_events(session, limit=max(0, limit - reset))
        session.commit()
    return {"reset": reset, "exhausted": exhausted}
