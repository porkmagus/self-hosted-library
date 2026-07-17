from datetime import datetime, timezone

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
from api.services.ingestion_recovery import recover_reconciliation_events


def test_recovery_requeues_published_activation_and_cleanup_until_reconciled() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        activating = Book(
            uuid="11111111-1111-1111-1111-111111111111",
            title="Activating",
            original_filename="a.txt",
            sanitized_filename="a.txt",
            file_extension=".txt",
            status=BookStatus.EMBEDDING,
            processing_generation=2,
            indexed_generation=1,
        )
        deleting = Book(
            uuid="22222222-2222-2222-2222-222222222222",
            title="Deleting",
            original_filename="d.txt",
            sanitized_filename="d.txt",
            file_extension=".txt",
            status=BookStatus.DELETED,
            deleted_at=datetime.now(timezone.utc),
        )
        session.add_all([activating, deleting])
        session.flush()
        succeeded = IngestionJob(
            uuid="33333333-3333-3333-3333-333333333333",
            book_id=activating.id,
            generation=2,
            state=IngestionState.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
        )
        cancelling = IngestionJob(
            uuid="44444444-4444-4444-4444-444444444444",
            book_id=deleting.id,
            state=IngestionState.CANCEL_REQUESTED,
            stage=IngestionStage.IMAGES,
        )
        session.add_all([succeeded, cancelling])
        session.flush()
        session.add_all(
            [
                IngestionOutbox(
                    uuid="55555555-5555-5555-5555-555555555555",
                    job_uuid=succeeded.uuid,
                    aggregate_type="job",
                    aggregate_uuid=succeeded.uuid,
                    event_type="activate_generation",
                    state=OutboxState.PUBLISHED,
                ),
                IngestionOutbox(
                    uuid="66666666-6666-6666-6666-666666666666",
                    aggregate_type="book",
                    aggregate_uuid=deleting.uuid,
                    event_type="cleanup_book",
                    state=OutboxState.PUBLISHED,
                ),
            ]
        )
        session.commit()

        reset = recover_reconciliation_events(session, limit=10)
        session.commit()

        states = [event.state for event in session.query(IngestionOutbox).all()]
        assert reset == 2
        assert states == [OutboxState.PENDING, OutboxState.PENDING]


def test_recovery_requeues_activation_cleanup_after_postgres_commit() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        book = Book(
            uuid="77777777-7777-7777-7777-777777777777",
            title="Pending cleanup",
            original_filename="a.txt",
            sanitized_filename="a.txt",
            file_extension=".txt",
            status=BookStatus.INDEXED,
            processing_generation=3,
            indexed_generation=3,
            indexed_job_uuid="88888888-8888-8888-8888-888888888888",
            indexed_activation_token="winner",
            activation_cleanup_pending=True,
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="88888888-8888-8888-8888-888888888888",
            book_id=book.id,
            generation=3,
            state=IngestionState.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
        )
        session.add(job)
        session.flush()
        event = IngestionOutbox(
            uuid="99999999-9999-9999-9999-999999999999",
            job_uuid=job.uuid,
            aggregate_type="job",
            aggregate_uuid=job.uuid,
            event_type="activate_generation",
            state=OutboxState.PUBLISHED,
        )
        session.add(event)
        session.commit()

        assert recover_reconciliation_events(session) == 1
        session.commit()
        assert event.state == OutboxState.PENDING
