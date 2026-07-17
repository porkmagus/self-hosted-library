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
)
from api.services.ingestion_jobs import (
    LeaseUnavailable,
    StaleLease,
    cancel_job,
    claim_job,
    complete_job,
    fail_job,
    record_checkpoint,
    release_for_retry,
    renew_lease,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _session(*, chunks: int = 10) -> tuple[Session, str]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book = Book(
        uuid="11111111-1111-1111-1111-111111111111",
        title="Lease Test",
        original_filename="lease.txt",
        sanitized_filename="lease.txt",
        file_extension=".txt",
        status=BookStatus.PENDING,
    )
    session.add(book)
    session.flush()
    job_uuid = "22222222-2222-2222-2222-222222222222"
    session.add(
        IngestionJob(
            uuid=job_uuid,
            book_id=book.id,
            generation=1,
            state=IngestionState.PENDING,
            stage=(IngestionStage.EMBEDDING if chunks else IngestionStage.SOURCE_READY),
            total_chunks=chunks,
        )
    )
    session.commit()
    return session, job_uuid


def test_claim_uses_monotonic_token_and_epoch() -> None:
    session, job_uuid = _session()
    first = claim_job(session, job_uuid, "worker-a", lease_seconds=30, now=NOW)
    session.commit()

    with pytest.raises(LeaseUnavailable):
        claim_job(
            session,
            job_uuid,
            "worker-b",
            lease_seconds=30,
            now=NOW + timedelta(seconds=10),
        )

    second = claim_job(
        session,
        job_uuid,
        "worker-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=31),
    )
    assert first.token != second.token
    assert first.claim_epoch == 1
    assert second.claim_epoch == 2


def test_every_checkpoint_is_fenced_by_token_and_epoch() -> None:
    session, job_uuid = _session()
    stale = claim_job(session, job_uuid, "worker-a", lease_seconds=30, now=NOW)
    session.commit()
    current = claim_job(
        session,
        job_uuid,
        "worker-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=31),
    )

    with pytest.raises(StaleLease):
        record_checkpoint(
            session,
            stale,
            expected_chunk_index=0,
            next_chunk_index=2,
            indexed_chunks=2,
            now=NOW + timedelta(seconds=32),
        )

    record_checkpoint(
        session,
        current,
        expected_chunk_index=0,
        next_chunk_index=2,
        indexed_chunks=2,
        now=NOW + timedelta(seconds=32),
    )
    session.commit()
    job = session.query(IngestionJob).filter_by(uuid=job_uuid).one()
    assert job.next_chunk_index == 2


def test_renewal_and_retry_transition_require_current_fence() -> None:
    session, job_uuid = _session()
    lease = claim_job(session, job_uuid, "worker-a", lease_seconds=30, now=NOW)
    renewed = renew_lease(
        session,
        lease,
        lease_seconds=30,
        now=NOW + timedelta(seconds=5),
    )
    assert renewed == NOW + timedelta(seconds=35)

    release_for_retry(
        session,
        lease,
        error_code="qdrant_unavailable",
        error_message="temporary outage",
        error_class="OSError",
        retry_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(seconds=6),
    )
    session.commit()
    job = session.query(IngestionJob).filter_by(uuid=job_uuid).one()
    assert job.state == IngestionState.RETRY_WAIT
    assert job.lease_token is None


def test_cancellation_fences_active_worker() -> None:
    session, job_uuid = _session()
    lease = claim_job(session, job_uuid, "worker-a", lease_seconds=30, now=NOW)
    assert cancel_job(session, job_uuid, now=NOW + timedelta(seconds=1)) is True
    session.commit()

    with pytest.raises(StaleLease):
        record_checkpoint(
            session,
            lease,
            expected_chunk_index=0,
            next_chunk_index=1,
            indexed_chunks=1,
            now=NOW + timedelta(seconds=2),
        )
    job = session.query(IngestionJob).filter_by(uuid=job_uuid).one()
    assert job.state == IngestionState.CANCEL_REQUESTED
    assert job.claim_epoch == lease.claim_epoch + 1


def test_completion_and_failure_are_mutually_fenced() -> None:
    session, job_uuid = _session(chunks=0)
    session.query(IngestionJob).filter_by(uuid=job_uuid).update(
        {"stage": IngestionStage.IMAGES}
    )
    session.commit()
    lease = claim_job(session, job_uuid, "worker-a", lease_seconds=30, now=NOW)
    complete_job(session, lease, now=NOW + timedelta(seconds=1))
    session.commit()

    job = session.query(IngestionJob).filter_by(uuid=job_uuid).one()
    assert job.state == IngestionState.SUCCEEDED
    assert job.finished_at is not None
    with pytest.raises(StaleLease):
        fail_job(
            session,
            lease,
            error_code="late_failure",
            error_message="must not overwrite success",
            error_class="RuntimeError",
            now=NOW + timedelta(seconds=2),
        )
