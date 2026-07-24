import io
from types import SimpleNamespace
from typing import IO

import pytest
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
from api.services.ingestion_submit import submit_uploaded_book


class RecordingStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_stream(
        self,
        object_name: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str,
    ) -> None:
        if self.fail:
            raise OSError("storage unavailable")
        payload = data.read()
        assert len(payload) == length
        self.objects[object_name] = payload

    def stat(self, object_name: str) -> SimpleNamespace:
        return SimpleNamespace(size=len(self.objects[object_name]))

    def delete(self, object_name: str) -> None:
        self.objects.pop(object_name, None)
        self.deleted.append(object_name)

    def download_to(self, object_name: str, destination) -> None:
        destination.write_bytes(self.objects[object_name])


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_upload_creates_job_and_outbox_in_one_acceptance_transaction() -> None:
    session = _session()
    store = RecordingStore()

    result = submit_uploaded_book(
        session,
        filename="my_book.txt",
        content_type="text/plain",
        stream=io.BytesIO(b"durable payload"),
        store=store,
    )

    book = session.query(Book).filter_by(uuid=result.book_uuid).one()
    job = session.query(IngestionJob).filter_by(uuid=result.job_uuid).one()
    event = session.query(IngestionOutbox).filter_by(job_uuid=result.job_uuid).one()
    assert book.status == BookStatus.PENDING
    assert book.source_object_key in store.objects
    assert job.state == IngestionState.PENDING
    assert job.stage == IngestionStage.SOURCE_READY
    assert event.state == OutboxState.PENDING
    assert result.task_id == result.job_uuid


def test_same_filename_different_content_uses_immutable_keys() -> None:
    session = _session()
    store = RecordingStore()

    first = submit_uploaded_book(
        session,
        filename="same.txt",
        content_type="text/plain",
        stream=io.BytesIO(b"first"),
        store=store,
    )
    second = submit_uploaded_book(
        session,
        filename="same.txt",
        content_type="text/plain",
        stream=io.BytesIO(b"second"),
        store=store,
    )

    assert first.book_uuid != second.book_uuid
    keys = {book.source_object_key for book in session.query(Book).all()}
    assert len(keys) == 2


def test_same_content_adopts_canonical_book_and_cleans_loser_object() -> None:
    session = _session()
    store = RecordingStore()
    first = submit_uploaded_book(
        session,
        filename="first.txt",
        content_type="text/plain",
        stream=io.BytesIO(b"same bytes"),
        store=store,
    )
    second = submit_uploaded_book(
        session,
        filename="second.txt",
        content_type="text/plain",
        stream=io.BytesIO(b"same bytes"),
        store=store,
    )

    assert second.book_uuid == first.book_uuid
    assert second.job_uuid == first.job_uuid
    assert session.query(Book).count() == 1
    assert len(store.deleted) == 1


def test_storage_failure_persists_failed_reservation_without_job() -> None:
    session = _session()

    with pytest.raises(OSError, match="storage unavailable"):
        submit_uploaded_book(
            session,
            filename="broken.txt",
            content_type="text/plain",
            stream=io.BytesIO(b"payload"),
            store=RecordingStore(fail=True),
        )

    book = session.query(Book).one()
    assert book.status == BookStatus.FAILED
    assert session.query(IngestionJob).count() == 0
    assert session.query(IngestionOutbox).count() == 0


def test_same_size_corrupt_source_upload_is_rejected_before_acceptance() -> None:
    session = _session()

    class CorruptingStore(RecordingStore):
        def download_to(self, object_name: str, destination) -> None:
            payload = self.objects[object_name]
            destination.write_bytes(b"x" * len(payload))

    with pytest.raises(OSError, match="checksum"):
        submit_uploaded_book(
            session,
            filename="corrupt.txt",
            content_type="text/plain",
            stream=io.BytesIO(b"durable payload"),
            store=CorruptingStore(),
        )

    assert session.query(Book).one().status == BookStatus.FAILED
    assert session.query(IngestionJob).count() == 0


def test_submission_cannot_accept_reservation_claimed_by_cleanup() -> None:
    session = _session()

    class ReapedStore(RecordingStore):
        def stat(self, object_name: str) -> SimpleNamespace:
            size = len(self.objects[object_name])
            reservation = (
                session.query(Book).filter_by(status=BookStatus.UPLOADING).one()
            )
            reservation.status = BookStatus.FAILED
            reservation.error_message = "stale upload reservation cleaned"
            session.commit()
            self.delete(object_name)
            return SimpleNamespace(size=size)

    with pytest.raises(RuntimeError, match="no longer accepting uploads"):
        submit_uploaded_book(
            session,
            filename="slow.txt",
            content_type="text/plain",
            stream=io.BytesIO(b"slow payload"),
            store=ReapedStore(),
        )

    assert session.query(IngestionJob).count() == 0
