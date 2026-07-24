from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.models import (
    Base,
    Book,
    BookStatus,
    IngestionJob,
    IngestionOutbox,
    IngestionStage,
    IngestionState,
    OutboxState,
)
from api.services.ingestion_outbox import (
    claim_dispatch_events,
    create_book_event,
    create_dispatch_event,
    mark_publish_failed,
    mark_published,
)


def _session() -> tuple[Session, str, str]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book_uuid = "11111111-1111-1111-1111-111111111111"
    job_uuid = "22222222-2222-2222-2222-222222222222"
    book = Book(
        uuid=book_uuid,
        title="Outbox",
        original_filename="outbox.txt",
        sanitized_filename="outbox.txt",
        file_extension=".txt",
        status=BookStatus.PENDING,
    )
    session.add(book)
    session.flush()
    session.add(
        IngestionJob(
            uuid=job_uuid,
            book_id=book.id,
            state=IngestionState.PENDING,
            stage=IngestionStage.SOURCE_READY,
        )
    )
    session.commit()
    return session, book_uuid, job_uuid


def test_outbox_claim_failure_and_publish_are_durable() -> None:
    session, _, job_uuid = _session()
    event = create_dispatch_event(session, job_uuid)
    session.commit()

    claimed = claim_dispatch_events(session)
    session.commit()
    assert claimed[0].job_uuid == job_uuid
    assert session.get(IngestionOutbox, event.id).state == OutboxState.PUBLISHING

    assert mark_publish_failed(
        session,
        event.uuid,
        publish_claim_token=claimed[0].publish_claim_token,
        error="redis down",
        retry_seconds=0,
    )
    session.commit()
    reclaimed = claim_dispatch_events(session)[0]
    assert reclaimed.uuid == event.uuid
    session.commit()

    assert mark_published(
        session,
        event.uuid,
        publish_claim_token=reclaimed.publish_claim_token,
        task_id="celery-task",
    )
    session.commit()
    row = session.get(IngestionOutbox, event.id)
    assert row.state == OutboxState.PUBLISHED
    assert session.query(IngestionJob).one().celery_task_id == "celery-task"


def test_book_cleanup_event_does_not_require_job_uuid() -> None:
    session, book_uuid, _ = _session()
    event = create_book_event(session, book_uuid, "cleanup_book")
    session.commit()

    claimed = claim_dispatch_events(session)
    assert len(claimed) == 1
    assert claimed[0].job_uuid is None
    assert claimed[0].aggregate_type == "book"
    assert claimed[0].aggregate_uuid == book_uuid
    assert event.aggregate_uuid == book_uuid


def test_failed_publish_cas_does_not_change_job_task_id() -> None:
    session, _, job_uuid = _session()
    event = create_dispatch_event(session, job_uuid)
    event.state = OutboxState.PUBLISHED
    job = session.query(IngestionJob).filter_by(uuid=job_uuid).one()
    job.celery_task_id = "original"
    session.commit()

    assert not mark_published(
        session,
        event.uuid,
        publish_claim_token="not-the-owner",
        task_id="incorrect",
    )
    session.commit()

    assert session.query(IngestionJob).filter_by(uuid=job_uuid).one().celery_task_id == "original"


def test_stale_publisher_cannot_complete_or_fail_reclaimed_event() -> None:
    session, _, job_uuid = _session()
    event = create_dispatch_event(session, job_uuid)
    session.commit()
    old_claim = claim_dispatch_events(session)[0]
    row = session.get(IngestionOutbox, event.id)
    row.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.commit()

    new_claim = claim_dispatch_events(session, stale_after_seconds=1)[0]
    session.commit()
    assert new_claim.publish_claim_token != old_claim.publish_claim_token
    assert not mark_published(
        session,
        event.uuid,
        publish_claim_token=old_claim.publish_claim_token,
        task_id="stale",
    )
    assert not mark_publish_failed(
        session,
        event.uuid,
        publish_claim_token=old_claim.publish_claim_token,
        error="stale failure",
        retry_seconds=0,
    )
    assert mark_published(
        session,
        event.uuid,
        publish_claim_token=new_claim.publish_claim_token,
        task_id="current",
    )
