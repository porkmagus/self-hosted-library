"""Crash-safe upload reservation and durable ingestion submission."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.models import Book, BookStatus, IngestionJob, IngestionStage, IngestionState
from api.services.ingestion_outbox import create_dispatch_event
from api.services.path_svc import validate_filename


class UploadStore(Protocol):
    def upload_stream(
        self,
        object_name: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str,
    ) -> None: ...

    def stat(self, object_name: str) -> Any: ...

    def delete(self, object_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    book_uuid: str
    job_uuid: str
    task_id: str


class HashingReader:
    """Hash a seekable upload while the object-store client consumes it."""

    def __init__(self, source: IO[bytes]) -> None:
        self._source = source
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        self._digest.update(chunk)
        self.bytes_read += len(chunk)
        return chunk

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


def _stream_length(stream: IO[bytes]) -> int:
    original = stream.tell()
    stream.seek(0, 2)
    length = stream.tell()
    stream.seek(original)
    return length - original


def _embedding_signature() -> str:
    from api.config import settings

    return f"{settings.EMBED_MODEL}|dim={settings.EMBED_DIMENSION}|normalize=provider"


def submit_uploaded_book(
    session: Session,
    *,
    filename: str,
    content_type: str,
    stream: IO[bytes],
    store: UploadStore,
    title: str | None = None,
    author: str | None = None,
    local_path: str | None = None,
) -> SubmissionResult:
    """Reserve, stream, verify, then atomically create the job and outbox event.

    When local_path is provided (inbox files), skips object-store upload — the
    pipeline reads directly from disk, bypassing S3 round-trips.
    """
    safe_name = validate_filename(filename)
    extension = Path(safe_name).suffix.lower() or ".bin"
    book_uuid = str(uuid.uuid4())
    source_key = local_path or f"books/{book_uuid}/source{extension}"
    inferred_title = Path(safe_name).stem.replace("_", " ").strip() or safe_name

    # TX1: a durable reservation makes any post-upload crash discoverable.
    book = Book(
        uuid=book_uuid,
        title=title.strip() if title and title.strip() else inferred_title,
        author=author.strip() if author and author.strip() else None,
        original_filename=safe_name,
        sanitized_filename=safe_name[:150],
        file_extension=extension,
        source_object_key=source_key,
        status=BookStatus.UPLOADING,
        processing_generation=1,
    )
    session.add(book)
    session.commit()

    length = _stream_length(stream)
    hashing_stream = HashingReader(stream)
    if local_path:
        # Inbox file: hash directly from disk, skip object-store upload
        digest = hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
    else:
        try:
            store.upload_stream(
                source_key,
                hashing_stream,  # type: ignore[arg-type]
                length=length,
                content_type=content_type,
            )
            if hashing_stream.bytes_read != length:
                raise OSError(
                    f"Object store consumed {hashing_stream.bytes_read} of {length} bytes"
                )
            remote = store.stat(source_key)
            if int(remote.size) != length:
                raise OSError(
                    f"Object-store HEAD returned {remote.size} bytes, expected {length}"
                )
        except Exception as exc:
            session.rollback()
            failed = session.query(Book).filter_by(uuid=book_uuid).one()
            failed.status = BookStatus.FAILED
            failed.error_message = str(exc)[:4000]
            session.commit()
            raise
        digest = hashing_stream.hexdigest
    session.rollback()
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:digest))"),
            {"digest": digest},
        )

    reservation = session.query(Book).filter_by(uuid=book_uuid).with_for_update().one()
    if reservation.status != BookStatus.UPLOADING:
        raise RuntimeError("Upload reservation is no longer accepting uploads")
    if not local_path:
        accepted_remote = store.stat(source_key)
        if int(accepted_remote.size) != length:
            raise OSError(
                f"Object-store HEAD returned {accepted_remote.size} bytes during acceptance, expected {length}"
            )
    canonical = (
        session.query(Book)
        .filter(
            Book.file_hash == digest,
            Book.id != reservation.id,
            Book.deleted_at.is_(None),
        )
        .first()
    )
    if canonical is not None:
        canonical_job = (
            session.query(IngestionJob)
            .filter(IngestionJob.book_id == canonical.id)
            .order_by(IngestionJob.generation.desc())
            .first()
        )
        session.delete(reservation)
        session.commit()
        if not local_path:
            store.delete(source_key)
        if canonical_job is None:
            raise RuntimeError("Canonical upload exists without an ingestion job")
        return SubmissionResult(
            book_uuid=canonical.uuid,
            job_uuid=canonical_job.uuid,
            task_id=canonical_job.uuid,
        )

    reservation.file_size_bytes = length
    reservation.file_hash = digest
    reservation.status = BookStatus.PENDING
    job_uuid = str(uuid.uuid4())
    job = IngestionJob(
        uuid=job_uuid,
        book_id=reservation.id,
        generation=reservation.processing_generation,
        state=IngestionState.PENDING,
        stage=IngestionStage.SOURCE_READY,
        source_hash=digest,
        embedding_model=_embedding_signature().split("|", 1)[0],
        embedding_dimension=int(
            _embedding_signature().split("dim=", 1)[1].split("|", 1)[0]
        ),
        embedding_signature=_embedding_signature(),
    )
    session.add(job)
    session.flush()
    create_dispatch_event(session, job_uuid)
    session.commit()
    return SubmissionResult(book_uuid=book_uuid, job_uuid=job_uuid, task_id=job_uuid)
