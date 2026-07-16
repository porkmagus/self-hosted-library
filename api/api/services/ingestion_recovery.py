"""Durable recovery for reconciliation work after bounded Celery retries."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.models import (
    Book,
    IngestionJob,
    IngestionOutbox,
    IngestionState,
    OutboxState,
)


def recover_reconciliation_events(session: Session, *, limit: int = 100) -> int:
    """Requeue published reconciliation messages until PostgreSQL records completion."""
    now = session.execute(select(func.now())).scalar_one()
    events = list(
        session.execute(
            select(IngestionOutbox)
            .where(
                IngestionOutbox.state == OutboxState.PUBLISHED,
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
                            .join(IngestionJob, IngestionJob.book_id == Book.id)
                            .where(
                                Book.deleted_at.is_not(None),
                                IngestionJob.state == IngestionState.CANCEL_REQUESTED,
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
        event.last_error = None
    session.flush()
    return len(events)
