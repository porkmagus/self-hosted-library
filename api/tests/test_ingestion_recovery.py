from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.models import (
    Base,
    Book,
    BookStatus,
    IngestionJob,
    IngestionStage,
    IngestionState,
    OutboxState,
)
from api.services.ingestion_outbox import (
    create_book_event,
    create_dispatch_event,
    create_event,
    reopen_lifecycle_event,
)
from api.services.ingestion_recovery import (
    recover_reconciliation_events,
    requeue_failed_jobs,
)
from api.tasks import celery_app as celery_tasks


def test_transient_failure_uses_outbox_instead_of_celery_retry(monkeypatch) -> None:
    def fail_pipeline(_job_uuid, _owner):
        raise OSError("temporary")

    def unexpected_retry(**_kwargs):
        raise AssertionError("durable retry publication must be outbox-driven")

    monkeypatch.setattr(celery_tasks, "run_ingestion_pipeline", fail_pipeline)
    monkeypatch.setattr(
        celery_tasks, "_transition_failure", lambda *args, **kwargs: (True, 300)
    )
    monkeypatch.setattr(celery_tasks.ingest_job_task, "retry", unexpected_retry)

    result = celery_tasks.ingest_job_task.run(
        "11111111-1111-1111-1111-111111111111"
    )

    assert result == {
        "job_uuid": "11111111-1111-1111-1111-111111111111",
        "status": "retry_scheduled",
        "retry_in": 300,
    }


def test_lifecycle_failure_transactionally_reopens_outbox() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        event = create_book_event(
            session,
            "11111111-1111-1111-1111-111111111111",
            "cleanup_book",
        )
        event.state = OutboxState.PUBLISHED
        session.commit()

        assert reopen_lifecycle_event(
            session,
            aggregate_uuid=event.aggregate_uuid,
            event_type=event.event_type,
            error="temporary object-store outage",
        )
        session.commit()

        assert event.state == OutboxState.PENDING
        assert event.published_at is None
        assert event.publish_claim_token is None
        assert "temporary object-store outage" in (event.last_error or "")


def test_transient_failure_atomically_reopens_dispatch_outbox(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        book = Book(
            uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Retryable",
            original_filename="retryable.pdf",
            sanitized_filename="retryable.pdf",
            file_extension=".pdf",
            file_hash="b" * 64,
            source_object_key="/app/data/retryable.pdf",
            processing_generation=1,
            status=BookStatus.EMBEDDING,
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            book_id=book.id,
            generation=1,
            state=IngestionState.RUNNING,
            stage=IngestionStage.EMBEDDING,
            attempt_count=3,
            max_attempts=8,
            lease_owner="worker:task",
            lease_token="cccccccc-cccc-cccc-cccc-cccccccccccc",
            claim_epoch=3,
            lease_expires_at=now + timedelta(minutes=5),
        )
        session.add(job)
        event = create_dispatch_event(session, job.uuid)
        event.state = OutboxState.PUBLISHED
        event.published_at = now
        session.commit()

        @contextmanager
        def test_session():
            yield session

        monkeypatch.setattr(celery_tasks, "get_db_session", test_session)

        transitioned, delay = celery_tasks._transition_failure(
            job.uuid,
            "worker:task",
            OSError("temporary"),
            terminal=False,
            error_code="transient_dependency_failure",
        )

        session.refresh(job)
        session.refresh(event)
        assert transitioned is True
        assert delay == 20
        assert job.state == IngestionState.RETRY_WAIT
        assert event.state == OutboxState.PENDING
        assert event.available_at == job.retry_at
        assert event.claimed_at is None
        assert event.published_at is None


def test_terminal_durable_failure_does_not_emit_celery_retry(monkeypatch) -> None:
    def fail_pipeline(_job_uuid, _owner):
        raise OSError("permanent after max attempts")

    def unexpected_retry(**_kwargs):
        raise AssertionError("terminal jobs must not emit a Celery retry")

    monkeypatch.setattr(celery_tasks, "run_ingestion_pipeline", fail_pipeline)
    monkeypatch.setattr(
        celery_tasks, "_transition_failure", lambda *args, **kwargs: (True, None)
    )
    monkeypatch.setattr(celery_tasks.ingest_job_task, "retry", unexpected_retry)

    result = celery_tasks.ingest_job_task.run(
        "22222222-2222-2222-2222-222222222222"
    )

    assert result["status"] == "failed"


def test_failed_job_requeue_preserves_artifacts_and_checkpoints() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)

    with Session(engine) as session:
        book = Book(
            uuid="11111111-1111-1111-1111-111111111111",
            title="Recoverable",
            original_filename="recoverable.pdf",
            sanitized_filename="recoverable.pdf",
            file_extension=".pdf",
            file_hash="a" * 64,
            source_object_key="/app/data/recoverable.pdf",
            processing_generation=1,
            status=BookStatus.FAILED,
            error_message="embedding unavailable",
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="22222222-2222-2222-2222-222222222222",
            book_id=book.id,
            generation=1,
            state=IngestionState.FAILED,
            stage=IngestionStage.EMBEDDING,
            source_hash=book.file_hash,
            attempt_count=8,
            max_attempts=8,
            claim_epoch=7,
            next_chunk_index=40,
            total_chunks=100,
            indexed_chunks=40,
            chunk_manifest_key="jobs/job/g1/chunks.json",
            chunk_manifest_sha256="b" * 64,
            error_code="dependency_unavailable",
            error_message="embedding unavailable",
            last_error_class="ConnectionError",
            celery_task_id="old-task",
            finished_at=now,
        )
        session.add(job)
        session.flush()
        event = create_dispatch_event(session, job.uuid)
        event.state = OutboxState.PUBLISHED
        event.published_at = now
        session.commit()

        result = requeue_failed_jobs(
            session,
            [job.uuid],
            now=now,
        )
        session.commit()

        assert result == [job.uuid]
        session.refresh(book)
        session.refresh(job)
        session.refresh(event)
        assert book.status == BookStatus.EMBEDDING
        assert book.error_message is None
        assert job.state == IngestionState.PENDING
        assert job.attempt_count == 0
        assert job.max_attempts == 8
        assert job.claim_epoch == 7
        assert job.next_chunk_index == 40
        assert job.indexed_chunks == 40
        assert job.chunk_manifest_key == "jobs/job/g1/chunks.json"
        assert job.chunk_manifest_sha256 == "b" * 64
        assert job.celery_task_id is None
        assert job.finished_at is None
        assert job.error_code is None
        assert job.error_message is None
        assert job.last_error_class is None
        assert event.state == OutboxState.PENDING
        assert event.available_at.replace(tzinfo=timezone.utc) == now
        assert event.claimed_at is None
        assert event.published_at is None
        assert event.last_error is None
        book.status = BookStatus.FAILED
        book.indexed_generation = 1
        book.indexed_job_uuid = job.uuid
        job.state = IngestionState.FAILED
        session.commit()

        with pytest.raises(ValueError, match="already has an indexed generation"):
            requeue_failed_jobs(session, [job.uuid], now=now)


def test_durable_recovery_reconciles_lifecycle_outbox(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    calls: list[Session] = []

    with Session(engine) as session:
        @contextmanager
        def sessions():
            yield session

        monkeypatch.setattr(celery_tasks, "get_db_session", sessions)
        monkeypatch.setattr(
            celery_tasks,
            "recover_reconciliation_events",
            lambda value, **_kwargs: calls.append(value) or 2,
        )

        result = celery_tasks.recover_durable_jobs.run(limit=0)

        assert calls == [session]
        assert result == {"reset": 0, "exhausted": 0, "reconciled": 2}


def test_lifecycle_time_limits_are_below_reconciliation_grace() -> None:
    for task in (
        celery_tasks.activate_generation_task,
        celery_tasks.cleanup_book_task,
        celery_tasks.dispatch_outbox,
        celery_tasks.recover_durable_jobs,
    ):
        assert task.time_limit is not None
        assert task.time_limit < 300
    assert celery_tasks.ingest_job_task.time_limit is None


@pytest.mark.parametrize(
    ("published_age_hours", "expected_state", "expected_reset"),
    [(0, OutboxState.PUBLISHED, 0), (25, OutboxState.PENDING, 1)],
)
def test_recovery_republishes_only_very_stale_pending_dispatch(
    monkeypatch,
    published_age_hours: int,
    expected_state: OutboxState,
    expected_reset: int,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        book = Book(
            uuid="33333333-3333-3333-3333-333333333333",
            title="Already queued",
            original_filename="queued.pdf",
            sanitized_filename="queued.pdf",
            file_extension=".pdf",
            file_hash="c" * 64,
            source_object_key="/app/data/inbox/queued.pdf",
            status=BookStatus.PENDING,
            processing_generation=1,
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="44444444-4444-4444-4444-444444444444",
            book_id=book.id,
            generation=1,
            state=IngestionState.PENDING,
            stage=IngestionStage.SOURCE_READY,
            source_hash=book.file_hash,
            embedding_model="bge-small",
            embedding_dimension=384,
            embedding_signature="bge-small|dim=384",
            celery_task_id="already-published-task",
        )
        session.add(job)
        session.flush()
        event = create_dispatch_event(session, job.uuid)
        event.state = OutboxState.PUBLISHED
        event.published_at = now - timedelta(hours=published_age_hours)
        session.commit()

        @contextmanager
        def test_session():
            yield session

        monkeypatch.setattr(celery_tasks, "get_db_session", test_session)

        result = celery_tasks.recover_durable_jobs.run(limit=100)

        assert result["reset"] == expected_reset
        assert event.state == expected_state


@pytest.mark.parametrize(
    ("published_age_hours", "expected_state", "expected_reconciled"),
    [(0, OutboxState.PUBLISHED, 0), (25, OutboxState.PENDING, 1)],
)
def test_recovery_republishes_only_stale_activation(
    monkeypatch,
    published_age_hours: int,
    expected_state: OutboxState,
    expected_reconciled: int,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        book = Book(
            uuid="55555555-5555-5555-5555-555555555555",
            title="Awaiting activation",
            original_filename="activation.pdf",
            sanitized_filename="activation.pdf",
            file_extension=".pdf",
            file_hash="d" * 64,
            source_object_key="/app/data/inbox/activation.pdf",
            status=BookStatus.EMBEDDING,
            processing_generation=1,
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="66666666-6666-6666-6666-666666666666",
            book_id=book.id,
            generation=1,
            state=IngestionState.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            source_hash=book.file_hash,
            embedding_model="bge-small",
            embedding_dimension=384,
            embedding_signature="bge-small|dim=384",
        )
        session.add(job)
        session.flush()
        event = create_event(session, job.uuid, "activate_generation")
        event.state = OutboxState.PUBLISHED
        event.published_at = now - timedelta(hours=published_age_hours)
        session.commit()

        @contextmanager
        def test_session():
            yield session

        monkeypatch.setattr(celery_tasks, "get_db_session", test_session)

        result = celery_tasks.recover_durable_jobs.run(limit=100)

        assert result["reset"] == 0
        assert result["reconciled"] == expected_reconciled
        assert event.state == expected_state
        if expected_reconciled:
            event.state = OutboxState.PUBLISHED
            event.published_at = now - timedelta(hours=25)
            session.commit()
            second = celery_tasks.recover_durable_jobs.run(limit=100)
            assert second["reconciled"] == 0
            assert event.state == OutboxState.PUBLISHED


def test_stale_cleanup_for_deleted_book_recovers_independent_of_job_state() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        book = Book(
            uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Deleted",
            original_filename="deleted.pdf",
            sanitized_filename="deleted.pdf",
            file_extension=".pdf",
            file_hash="a" * 64,
            source_object_key="source",
            status=BookStatus.DELETED,
            processing_generation=2,
            deleted_at=now,
        )
        session.add(book)
        session.flush()
        session.add(
            IngestionJob(
                uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                book_id=book.id,
                generation=1,
                state=IngestionState.SUCCEEDED,
                stage=IngestionStage.FINALIZING,
                source_hash=book.file_hash,
                embedding_model="bge-small",
                embedding_dimension=384,
                embedding_signature="bge-small|dim=384",
            )
        )
        event = create_book_event(session, book.uuid, "cleanup_book")
        event.state = OutboxState.PUBLISHED
        event.published_at = now - timedelta(minutes=10)
        session.commit()

        assert recover_reconciliation_events(session, stale_seconds=300) == 1
        assert event.state == OutboxState.PENDING


def test_completed_cleanup_is_not_reconciled() -> None:
    assert "cleanup_completed_at" in Book.__table__.columns


def test_expired_running_job_is_recovered_only_once(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        book = Book(
            uuid="77777777-7777-7777-7777-777777777777",
            title="Expired worker lease",
            original_filename="expired.pdf",
            sanitized_filename="expired.pdf",
            file_extension=".pdf",
            file_hash="e" * 64,
            source_object_key="/app/data/inbox/expired.pdf",
            status=BookStatus.EMBEDDING,
            processing_generation=1,
        )
        session.add(book)
        session.flush()
        job = IngestionJob(
            uuid="88888888-8888-8888-8888-888888888888",
            book_id=book.id,
            generation=1,
            state=IngestionState.RUNNING,
            stage=IngestionStage.EMBEDDING,
            source_hash=book.file_hash,
            embedding_model="bge-small",
            embedding_dimension=384,
            embedding_signature="bge-small|dim=384",
            attempt_count=1,
            max_attempts=5,
            lease_owner="dead-worker",
            lease_token="99999999-9999-9999-9999-999999999999",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        session.add(job)
        session.flush()
        event = create_dispatch_event(session, job.uuid)
        event.state = OutboxState.PUBLISHED
        event.published_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()

        @contextmanager
        def test_session():
            yield session

        monkeypatch.setattr(celery_tasks, "get_db_session", test_session)

        first = celery_tasks.recover_durable_jobs.run(limit=100)

        assert first["reset"] == 1
        assert job.state == IngestionState.PENDING
        assert job.lease_owner is None
        assert job.lease_token is None
        assert job.lease_expires_at is None
        assert event.state == OutboxState.PENDING

        event.state = OutboxState.PUBLISHED
        session.commit()

        second = celery_tasks.recover_durable_jobs.run(limit=100)

        assert second["reset"] == 0
        assert event.state == OutboxState.PUBLISHED
