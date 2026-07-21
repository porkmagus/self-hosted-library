"""Transactional outbox for reliable PostgreSQL-to-Celery dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from api.models import IngestionOutbox, OutboxState


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    uuid: str
    job_uuid: str | None
    aggregate_type: str
    aggregate_uuid: str
    event_type: str


def create_event(session: Session, job_uuid: str, event_type: str) -> IngestionOutbox:
    event = IngestionOutbox(
        uuid=str(uuid4()),
        job_uuid=job_uuid,
        aggregate_type="job",
        aggregate_uuid=job_uuid,
        event_type=event_type,
        state=OutboxState.PENDING,
    )
    session.add(event)
    session.flush()
    return event


def create_dispatch_event(session: Session, job_uuid: str) -> IngestionOutbox:
    return create_event(session, job_uuid, "dispatch_ingestion")


def create_book_event(
    session: Session, book_uuid: str, event_type: str
) -> IngestionOutbox:
    event = IngestionOutbox(
        uuid=str(uuid4()),
        job_uuid=None,
        aggregate_type="book",
        aggregate_uuid=book_uuid,
        event_type=event_type,
        state=OutboxState.PENDING,
    )
    session.add(event)
    session.flush()
    return event


def claim_dispatch_events(
    session: Session,
    *,
    limit: int = 100,
    stale_after_seconds: int = 300,
) -> list[OutboxEvent]:
    """Claim a bounded batch; PostgreSQL scanners use SKIP LOCKED."""
    now = session.execute(select(func.now())).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=stale_after_seconds)
    query = (
        select(IngestionOutbox)
        .where(
            IngestionOutbox.available_at <= now,
            or_(
                IngestionOutbox.state == OutboxState.PENDING,
                (
                    (IngestionOutbox.state == OutboxState.PUBLISHING)
                    & (IngestionOutbox.claimed_at < stale_before)
                ),
            ),
        )
        .order_by(
            # Priority: activation/cleanup before ingestion dispatch
            (IngestionOutbox.event_type != "dispatch_ingestion").desc(),
            IngestionOutbox.id,
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(session.execute(query).scalars())
    events = [
        OutboxEvent(
            row.uuid,
            row.job_uuid,
            row.aggregate_type,
            row.aggregate_uuid,
            row.event_type,
        )
        for row in rows
    ]
    for row in rows:
        row.state = OutboxState.PUBLISHING
        row.claimed_at = now
        row.attempts += 1
    session.flush()
    return events


def mark_published(session: Session, event_uuid: str, *, task_id: str) -> bool:
    now = session.execute(select(func.now())).scalar_one()
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionOutbox)
        .where(
            IngestionOutbox.uuid == event_uuid,
            IngestionOutbox.state == OutboxState.PUBLISHING,
        )
        .values(state=OutboxState.PUBLISHED, published_at=now, last_error=None)
    )
    # Celery task IDs are diagnostic; duplicates are expected after uncertain publish.
    from api.models import IngestionJob

    event = session.execute(
        select(IngestionOutbox).where(IngestionOutbox.uuid == event_uuid)
    ).scalar_one_or_none()
    if event is not None and event.job_uuid is not None:
        session.execute(
            update(IngestionJob)
            .where(IngestionJob.uuid == event.job_uuid)
            .values(celery_task_id=task_id)
        )
    session.flush()
    return result.rowcount == 1


def mark_publish_failed(
    session: Session,
    event_uuid: str,
    *,
    error: str,
    retry_seconds: int,
) -> bool:
    now = session.execute(select(func.now())).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionOutbox)
        .where(
            IngestionOutbox.uuid == event_uuid,
            IngestionOutbox.state == OutboxState.PUBLISHING,
        )
        .values(
            state=OutboxState.PENDING,
            available_at=now + timedelta(seconds=retry_seconds),
            claimed_at=None,
            last_error=error[:4000],
        )
    )
    session.flush()
    return result.rowcount == 1
