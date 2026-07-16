"""Real-PostgreSQL fencing tests; set TEST_DATABASE_URL to enable."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from api.models import Book, BookStatus, IngestionJob, IngestionStage, IngestionState
from api.services.ingestion_jobs import (
    LeaseUnavailable,
    StaleLease,
    claim_job,
    record_checkpoint,
)
from api.services.ingestion_pipeline import _lock_current_book

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def test_only_one_concurrent_claim_wins_and_stale_epoch_cannot_checkpoint() -> None:
    assert DATABASE_URL
    engine = create_engine(DATABASE_URL)
    job_uuid = "22222222-2222-2222-2222-222222222222"
    with Session(engine) as session:
        session.execute(
            text("TRUNCATE ingestion_outbox, ingestion_jobs, books RESTART IDENTITY")
        )
        book = Book(
            uuid="11111111-1111-1111-1111-111111111111",
            title="Concurrent claim",
            original_filename="claim.txt",
            sanitized_filename="claim.txt",
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
                stage=IngestionStage.EMBEDDING,
                total_chunks=10,
            )
        )
        session.commit()

    barrier_time = datetime.now(timezone.utc)

    def contender(owner: str):
        with Session(engine) as session:
            try:
                lease = claim_job(
                    session,
                    job_uuid,
                    owner,
                    lease_seconds=30,
                    now=barrier_time,
                )
                session.commit()
                return lease
            except LeaseUnavailable:
                session.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(contender, ["worker-a", "worker-b"]))
    winner = next(lease for lease in leases if lease is not None)
    assert sum(lease is not None for lease in leases) == 1

    with Session(engine) as session:
        replacement = claim_job(
            session,
            job_uuid,
            "replacement",
            lease_seconds=30,
            now=barrier_time + timedelta(seconds=31),
        )
        session.commit()
    assert replacement.claim_epoch == winner.claim_epoch + 1
    assert replacement.token != winner.token

    with Session(engine) as session:
        locked_book = _lock_current_book(session, job_uuid, replacement.generation)
        assert locked_book.uuid == "11111111-1111-1111-1111-111111111111"
        session.rollback()

    with Session(engine) as session, pytest.raises(StaleLease):
        record_checkpoint(
            session,
            winner,
            expected_chunk_index=0,
            next_chunk_index=1,
            indexed_chunks=1,
            now=barrier_time + timedelta(seconds=32),
        )

    with Session(engine) as session:
        record_checkpoint(
            session,
            replacement,
            expected_chunk_index=0,
            next_chunk_index=2,
            indexed_chunks=2,
            now=barrier_time + timedelta(seconds=32),
        )
        session.commit()
        job = session.query(IngestionJob).filter_by(uuid=replacement.job_uuid).one()
        assert job.text_batch_claims == [
            {"start": 0, "end": 2, "claim_epoch": replacement.claim_epoch}
        ]
