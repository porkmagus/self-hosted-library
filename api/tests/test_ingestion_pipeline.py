import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import IO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.models import (
    Base,
    Book,
    BookStatus,
    IngestionJob,
    IngestionOutbox,
    IngestionStage,
    IngestionState,
)
from api.services import ingestion_pipeline
from api.services.ingestion_activation import derive_expected_vector_points
from api.services.ingestion_jobs import JobLease, release_for_retry
from api.services.ingestion_pipeline import PipelineDependencies, run_ingestion_pipeline


def test_scanned_pdf_extraction_uses_ocr_only_after_native_text_is_empty(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan")
    monkeypatch.setattr(ingestion_pipeline, "extract_pdf_pymupdf", lambda _: "")
    monkeypatch.setattr(
        ingestion_pipeline,
        "extract_pdf_ocr",
        lambda path, output: "recovered searchable text",
    )

    assert ingestion_pipeline._extract(source, ".pdf", tmp_path) == (
        "recovered searchable text"
    )


@pytest.mark.parametrize(("extension", "extractor"), [(".epub", "extract_epub"), (".docx", "extract_docx")])
def test_malformed_archive_documents_are_terminal(
    tmp_path: Path, monkeypatch, extension: str, extractor: str
) -> None:
    source = tmp_path / f"broken{extension}"
    source.write_bytes(b"broken")

    def invalid(_path):
        raise ingestion_pipeline.DocumentParseError("invalid archive document")

    monkeypatch.setattr(ingestion_pipeline, extractor, invalid)

    with pytest.raises(ingestion_pipeline.InvalidDocumentError, match="invalid archive"):
        ingestion_pipeline._extract(source, extension, tmp_path)


class MemoryStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def download_to(self, key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[key])

    def upload_stream(
        self,
        key: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str,
    ) -> None:
        payload = data.read()
        assert len(payload) == length
        self.objects[key] = payload

    def stat(self, key: str) -> SimpleNamespace:
        return SimpleNamespace(size=len(self.objects[key]))


def test_upload_verified_rejects_existing_same_size_wrong_hash() -> None:
    store = MemoryStore({"artifact": b"wrong"})

    with pytest.raises(OSError, match="checksum mismatch"):
        ingestion_pipeline._upload_verified(
            store, "artifact", b"right", "application/octet-stream"
        )


def test_pipeline_resumes_after_last_committed_vector_batch(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    source = b"source document bytes"
    with factory() as session:
        book = Book(
            uuid="11111111-1111-1111-1111-111111111111",
            title="Book",
            original_filename="book.txt",
            sanitized_filename="book",
            file_extension=".txt",
            file_hash=hashlib.sha256(source).hexdigest(),
            source_object_key="books/book-uuid/source.txt",
            status=BookStatus.PENDING,
        )
        session.add(book)
        session.flush()
        session.add(
            IngestionJob(
                uuid="22222222-2222-2222-2222-222222222222",
                book_id=book.id,
                generation=1,
                state=IngestionState.PENDING,
                stage=IngestionStage.SOURCE_READY,
                source_hash=book.file_hash,
                embedding_model="embed-model",
                embedding_dimension=2,
                embedding_signature="embed-model|dim=2|normalize=provider",
            )
        )
        session.commit()

    calls: dict[str, list | int] = {
        "extract": 0,
        "chunk": 0,
        "upsert": [],
        "claim_epochs": [],
    }
    fail_second_batch = True

    def extract(path: Path, extension: str, output: Path) -> str:
        calls["extract"] += 1
        return (
            "A sufficiently long deterministic extracted document for restart testing."
        )

    def chunk(text: str) -> list[str]:
        calls["chunk"] += 1
        return [f"chunk {index} content" for index in range(5)]

    def embed_vectors(texts: list[str]) -> list[list[float] | None]:
        return [[float(index), 1.0] for index, _ in enumerate(texts)]

    def upsert(chunks, **kwargs) -> int:
        nonlocal fail_second_batch
        indices = [chunk.chunk_index for chunk in chunks]
        calls["upsert"].append(indices)
        calls["claim_epochs"].append(kwargs["claim_epoch"])
        if fail_second_batch and indices == [2, 3]:
            raise OSError("qdrant unavailable")
        return len(chunks)

    store = MemoryStore({"books/book-uuid/source.txt": source})
    deps = PipelineDependencies(
        sessions=sessions,
        store=store,
        extract=extract,
        chunk=chunk,
        embed_vectors=embed_vectors,
        upsert=upsert,
        extract_images=lambda *_: [],
        embed_image=lambda _: None,
        index_image=lambda *_args, **_kwargs: "unused",
    )
    monkeypatch.setattr(
        "api.services.ingestion_pipeline.settings.INGEST_CHECKPOINT_BATCH_SIZE", 2
    )

    with pytest.raises(OSError, match="qdrant unavailable"):
        run_ingestion_pipeline(
            "22222222-2222-2222-2222-222222222222",
            "worker-a",
            dependencies=deps,
        )

    with factory() as session:
        job = session.query(IngestionJob).one()
        assert job.next_chunk_index == 2
        assert job.indexed_chunks == 2
        lease = JobLease(
            job_uuid=job.uuid,
            generation=job.generation,
            owner=job.lease_owner or "",
            token=job.lease_token or "",
            claim_epoch=job.claim_epoch,
            expires_at=job.lease_expires_at,
            next_chunk_index=job.next_chunk_index,
            indexed_chunks=job.indexed_chunks,
        )
        release_for_retry(
            session,
            lease,
            error_code="qdrant_unavailable",
            error_message="temporary outage",
            error_class="OSError",
            retry_at=job.heartbeat_at,
            now=job.heartbeat_at,
        )
        session.commit()

    fail_second_batch = False
    result = run_ingestion_pipeline(
        "22222222-2222-2222-2222-222222222222",
        "worker-b",
        dependencies=deps,
    )

    assert result["status"] == "awaiting_activation"
    assert result["indexed_chunks"] == 5
    assert calls["extract"] == 1
    assert calls["chunk"] == 1
    assert calls["upsert"] == [[0, 1], [2, 3], [2, 3], [4]]
    assert calls["claim_epochs"] == [1, 1, 2, 2]
    with factory() as session:
        job = session.query(IngestionJob).one()
        book = session.query(Book).one()
        activation = (
            session.query(IngestionOutbox)
            .filter_by(job_uuid=job.uuid, event_type="activate_generation")
            .one()
        )
        assert job.state == IngestionState.SUCCEEDED
        assert job.next_chunk_index == 5
        assert job.text_batch_claims == [
            {"start": 0, "end": 2, "claim_epoch": 1},
            {"start": 2, "end": 4, "claim_epoch": 2},
            {"start": 4, "end": 5, "claim_epoch": 2},
        ]
        assert book.status == BookStatus.EMBEDDING
        assert activation.event_type == "activate_generation"


@pytest.mark.parametrize(
    ("extracted_text", "chunks", "expected_chunks"),
    [
        ("document text long enough to index", ["document chunk"], 1),
        ("tiny", [], 0),
    ],
)
def test_pdf_pipeline_persists_and_checkpoints_image_manifest(
    monkeypatch, extracted_text: str, chunks: list[str], expected_chunks: int
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    source = b"fake-pdf"
    with factory() as session:
        book = Book(
            uuid="33333333-3333-3333-3333-333333333333",
            title="Illustrated",
            original_filename="book.pdf",
            sanitized_filename="book.pdf",
            file_extension=".pdf",
            file_hash=hashlib.sha256(source).hexdigest(),
            source_object_key="books/illustrated/source.pdf",
            status=BookStatus.PENDING,
        )
        session.add(book)
        session.flush()
        session.add(
            IngestionJob(
                uuid="44444444-4444-4444-4444-444444444444",
                book_id=book.id,
                generation=1,
                state=IngestionState.PENDING,
                stage=IngestionStage.SOURCE_READY,
                source_hash=book.file_hash,
                embedding_model="embed-model",
                embedding_dimension=2,
                embedding_signature="embed-model|dim=2|normalize=provider",
            )
        )
        session.commit()

    image = {
        "image_id": "55555555-5555-5555-5555-555555555555",
        "book_id": "33333333-3333-3333-3333-333333333333",
        "book_title": "Illustrated",
        "page_number": 0,
        "image_bytes": b"image-payload",
        "image_sha256": hashlib.sha256(b"image-payload").hexdigest(),
        "ext": "png",
        "width": 64,
        "height": 48,
    }
    indexed_images: list[str] = []
    store = MemoryStore({"books/illustrated/source.pdf": source})
    deps = PipelineDependencies(
        sessions=sessions,
        store=store,
        extract=lambda *_: extracted_text,
        chunk=lambda _: chunks,
        embed_vectors=lambda values: [[0.1, 0.2] for _ in values],
        upsert=lambda chunks, **_: len(chunks),
        extract_images=lambda *_: [image],
        embed_image=lambda _: [0.3, 0.4],
        index_image=lambda value, _vector, **_: (
            indexed_images.append(value["image_id"]) or value["image_id"]
        ),
    )
    monkeypatch.setattr(
        "api.services.ingestion_pipeline.settings.INGEST_CHECKPOINT_BATCH_SIZE", 2
    )

    result = run_ingestion_pipeline(
        "44444444-4444-4444-4444-444444444444",
        "worker-images",
        dependencies=deps,
    )

    assert result["indexed_images"] == 1
    assert result["indexed_chunks"] == expected_chunks
    assert indexed_images == ["55555555-5555-5555-5555-555555555555"]
    with factory() as session:
        job = session.query(IngestionJob).one()
        book = session.query(Book).one()
        assert job.state == IngestionState.SUCCEEDED
        assert job.stage == IngestionStage.FINALIZING
        assert job.total_images == 1
        assert job.next_image_index == 1
        assert job.indexed_images == 1
        assert job.image_manifest_key in store.objects
        assert job.chunk_manifest_key in store.objects
        assert book.total_images == 1
        assert book.indexed_images == 1


def test_standalone_image_commits_empty_text_identity_for_activation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    source = b"standalone-image-payload"
    book_uuid = "66666666-6666-6666-6666-666666666666"
    job_uuid = "77777777-7777-7777-7777-777777777777"
    with factory() as session:
        book = Book(
            uuid=book_uuid,
            title="Standalone",
            original_filename="image.bmp",
            sanitized_filename="image.bmp",
            file_extension=".bmp",
            file_hash=hashlib.sha256(source).hexdigest(),
            source_object_key="books/standalone/source.bmp",
            status=BookStatus.PENDING,
        )
        session.add(book)
        session.flush()
        session.add(
            IngestionJob(
                uuid=job_uuid,
                book_id=book.id,
                generation=1,
                state=IngestionState.PENDING,
                stage=IngestionStage.SOURCE_READY,
                source_hash=book.file_hash,
                embedding_model="embed-model",
                embedding_dimension=2,
                embedding_signature="embed-model|dim=2|normalize=provider",
            )
        )
        session.commit()

    store = MemoryStore({"books/standalone/source.bmp": source})
    deps = PipelineDependencies(
        sessions=sessions,
        store=store,
        extract=lambda *_: pytest.fail("standalone images must not use text extraction"),
        chunk=lambda _: pytest.fail("standalone images must not use text chunking"),
        embed_vectors=lambda _: [],
        upsert=lambda chunks, **_: len(chunks),
        extract_images=lambda *_: [],
        embed_image=lambda _: [0.3, 0.4],
        index_image=lambda value, _vector, **_: value["image_id"],
    )

    result = run_ingestion_pipeline(job_uuid, "worker-image", dependencies=deps)

    assert result["indexed_chunks"] == 0
    assert result["indexed_images"] == 1
    with factory() as session:
        job = session.query(IngestionJob).one()
        assert job.chunk_manifest_key in store.objects
        assert job.chunk_manifest_sha256 == hashlib.sha256(
            store.objects[job.chunk_manifest_key]
        ).hexdigest()
        text_points, image_points = derive_expected_vector_points(
            job,
            book_uuid,
            store.objects[job.chunk_manifest_key],
            store.objects[job.image_manifest_key],
        )
        assert text_points == []
        assert len(image_points) == 1
