"""Restart-safe ingestion pipeline driven by the fenced PostgreSQL ledger."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.models import (
    Book,
    BookStatus,
    IngestionJob,
    IngestionStage,
    get_db_session,
)
from api.services.image_svc import (
    extract_images_from_pdf,
    get_image_embedding,
    index_image,
)
from api.services.ingestion_jobs import (
    JobLease,
    StaleLease,
    claim_job,
    complete_job,
    record_artifacts,
    record_checkpoint,
    record_image_checkpoint,
    renew_lease,
)
from api.services.ingestion_outbox import create_event
from api.services.object_store import get_object_store
from api.services.embedding_client import get_embedding_batch
from api.services.parser_svc import (
    chunk_text_semantic,
    convert_pdf_with_marker,
    extract_docx,
    extract_epub,
    extract_pdf_pymupdf,
    extract_text_file,
)
from api.services.qdrant_svc import EmbeddedChunk, upsert_ingestion_chunks

CHUNKER_VERSION = "semantic-v1-1000-200"
ARTIFACT_SCHEMA_VERSION = 1


class InvalidDocumentError(ValueError):
    """The source is readable but cannot produce a searchable artifact."""


class PipelineStore(Protocol):
    def exists(self, object_name: str) -> bool: ...

    def download_to(self, object_name: str, destination: Path) -> None: ...

    def upload_stream(
        self,
        object_name: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str,
    ) -> None: ...

    def stat(self, object_name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Fragment:
    chunk_index: int
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    book_id: int
    book_uuid: str
    book_title: str
    source_key: str
    source_hash: str
    extension: str
    generation: int
    stage: IngestionStage
    next_chunk_index: int
    indexed_chunks: int
    extracted_text_key: str | None
    extracted_text_sha256: str | None
    chunk_manifest_key: str | None
    chunk_manifest_sha256: str | None
    image_manifest_key: str | None
    image_manifest_sha256: str | None
    total_images: int
    next_image_index: int
    indexed_images: int
    embedding_signature: str


@dataclass(slots=True)
class PipelineDependencies:
    sessions: Callable[[], AbstractContextManager[Session]]
    store: PipelineStore
    extract: Callable[[Path, str, Path], str]
    chunk: Callable[[str], list[str]]
    embed_vectors: Callable[[list[str]], list[list[float] | None]]
    upsert: Callable[..., int]
    extract_images: Callable[[str, str, str], list[dict[str, Any]]]
    embed_image: Callable[[bytes], list[float] | None]
    index_image: Callable[..., str]


class LeaseWatchdog:
    """Renew a worker fence independently of slow parser/embedding calls."""

    def __init__(self, deps: PipelineDependencies, lease: JobLease) -> None:
        self._deps = deps
        self._lease = lease
        self._stop = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-{lease.job_uuid[:8]}",
            daemon=True,
        )

    def __enter__(self) -> LeaseWatchdog:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(1.0, settings.INGEST_LEASE_SECONDS / 3)
        while not self._stop.wait(interval):
            try:
                with self._deps.sessions() as session:
                    renew_lease(
                        session,
                        self._lease,
                        lease_seconds=settings.INGEST_LEASE_SECONDS,
                    )
                    session.commit()
            except Exception as exc:
                self._error = exc
                return

    def check(self) -> None:
        if self._error is not None:
            raise self._error


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract(path: Path, extension: str, output_dir: Path) -> str:
    if extension == ".pdf":
        markdown = convert_pdf_with_marker(path, output_dir)
        return (
            markdown.read_text(encoding="utf-8")
            if markdown and markdown.exists()
            else extract_pdf_pymupdf(path)
        )
    if extension == ".epub":
        return extract_epub(path)
    if extension in {".docx", ".doc"}:
        return extract_docx(path)
    if extension in {".txt", ".md", ".htm", ".html"}:
        return extract_text_file(path)
    # Standalone images - extract metadata as text for search
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}:
        meta = extract_image_metadata(path)
        if not meta:
            raise InvalidDocumentError("Image metadata extraction produced no data")
        # Build searchable text from metadata
        text_parts = [
            f"Image format: {meta.get('format', 'unknown')}",
            f"Size: {meta.get('width', 0)}x{meta.get('height', 0)}",
            f"Mode: {meta.get('mode', 'unknown')}",
        ]
        if "exif" in meta:
            text_parts.append(f"EXIF: {meta['exif']}")
        return " | ".join(text_parts)
    raise InvalidDocumentError(f"Unsupported format: {extension}")


def default_dependencies() -> PipelineDependencies:
    return PipelineDependencies(
        sessions=get_db_session,
        store=get_object_store(),
        extract=_extract,
        chunk=chunk_text_semantic,
        embed_vectors=get_embedding_batch,
        upsert=upsert_ingestion_chunks,
        extract_images=extract_images_from_pdf,
        embed_image=get_image_embedding,
        index_image=index_image,
    )


def _snapshot(session: Session, job_uuid: str) -> JobSnapshot:
    job = session.execute(
        select(IngestionJob).where(IngestionJob.uuid == job_uuid)
    ).scalar_one()
    book = session.get(Book, job.book_id)
    if book is None or not book.source_object_key or not job.source_hash:
        raise InvalidDocumentError("Job source metadata is incomplete")
    if not job.embedding_signature:
        raise InvalidDocumentError("Job embedding signature is missing")
    return JobSnapshot(
        book_id=book.id,
        book_uuid=book.uuid,
        book_title=book.title,
        source_key=book.source_object_key,
        source_hash=job.source_hash,
        extension=book.file_extension,
        generation=job.generation,
        stage=job.stage,
        next_chunk_index=job.next_chunk_index,
        indexed_chunks=job.indexed_chunks,
        extracted_text_key=job.extracted_text_key,
        extracted_text_sha256=job.extracted_text_sha256,
        chunk_manifest_key=job.chunk_manifest_key,
        chunk_manifest_sha256=job.chunk_manifest_sha256,
        image_manifest_key=job.image_manifest_key,
        image_manifest_sha256=job.image_manifest_sha256,
        total_images=job.total_images,
        next_image_index=job.next_image_index,
        indexed_images=job.indexed_images,
        embedding_signature=job.embedding_signature,
    )


def _lock_current_book(session: Session, job_uuid: str, generation: int) -> Book:
    book = session.execute(
        select(Book)
        .join(IngestionJob, IngestionJob.book_id == Book.id)
        .where(
            IngestionJob.uuid == job_uuid,
            Book.processing_generation == generation,
            Book.deleted_at.is_(None),
        )
        .with_for_update(of=Book)
    ).scalar_one_or_none()
    if book is None:
        raise StaleLease("Book generation is deleted or superseded")
    return book


def _upload_verified(
    store: PipelineStore,
    object_key: str,
    payload: bytes,
    content_type: str,
) -> None:
    if not store.exists(object_key):
        store.upload_stream(
            object_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
    if int(store.stat(object_key).size) != len(payload):
        raise OSError(f"Artifact HEAD verification failed for {object_key}")


def _read_verified_artifact(
    store: PipelineStore,
    key: str,
    expected_sha256: str,
    destination: Path,
) -> bytes:
    store.download_to(key, destination)
    payload = destination.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise OSError(f"Artifact checksum mismatch for {key}")
    return payload


def _split_fragment(
    text: str, *, path: str = "root", max_chars: int = 800
) -> list[tuple[str, str]]:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return [(path, normalized)]
    midpoint = len(normalized) // 2
    left_break = normalized.rfind(" ", 0, midpoint)
    right_break = normalized.find(" ", midpoint)
    split_at = left_break if left_break >= max_chars // 2 else right_break
    if split_at <= 0 or split_at >= len(normalized):
        split_at = midpoint
    return _split_fragment(
        normalized[:split_at], path=f"{path}.L", max_chars=max_chars
    ) + _split_fragment(normalized[split_at:], path=f"{path}.R", max_chars=max_chars)


def _build_manifest(chunks: list[str]) -> tuple[bytes, list[Fragment]]:
    fragments = [
        Fragment(chunk_index=index, path=path, text=fragment_text)
        for index, chunk in enumerate(chunks)
        for path, fragment_text in _split_fragment(chunk)
    ]
    document = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "canonical_chunks": chunks,
        "fragments": [
            {"chunk_index": item.chunk_index, "path": item.path, "text": item.text}
            for item in fragments
        ],
    }
    return (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        fragments,
    )


def _parse_manifest(payload: bytes) -> tuple[list[str], list[Fragment]]:
    document = json.loads(payload)
    if document.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise InvalidDocumentError("Unsupported chunk manifest schema")
    if document.get("chunker_version") != CHUNKER_VERSION:
        raise InvalidDocumentError("Chunk manifest version mismatch")
    chunks = [str(value) for value in document["canonical_chunks"]]
    fragments = [
        Fragment(
            chunk_index=int(value["chunk_index"]),
            path=str(value["path"]),
            text=str(value["text"]),
        )
        for value in document["fragments"]
    ]
    return chunks, fragments


def _build_image_manifest(
    images: list[dict[str, Any]],
    store: PipelineStore,
    *,
    book_uuid: str,
    generation: int,
) -> tuple[bytes, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    for image in images:
        payload = image.get("image_bytes")
        if not isinstance(payload, (bytes, bytearray)):
            raise InvalidDocumentError("Extracted image payload is not bytes")
        image_bytes = bytes(payload)
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        if image.get("image_sha256") not in {None, image_sha}:
            raise InvalidDocumentError("Extracted image checksum is inconsistent")
        ext = str(image["ext"]).lower()
        image_id = str(image["image_id"])
        object_key = (
            f"books/{book_uuid}/generations/{generation}/images/{image_id}.{ext}"
        )
        _upload_verified(store, object_key, image_bytes, f"image/{ext}")
        entries.append(
            {
                "image_id": image_id,
                "image_sha256": image_sha,
                "object_key": object_key,
                "ext": ext,
                "page_number": int(image.get("page_number", 0) or 0),
                "width": int(image.get("width", 0) or 0),
                "height": int(image.get("height", 0) or 0),
            }
        )
    document = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "images": entries,
    }
    return (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        entries,
    )


def _parse_image_manifest(payload: bytes) -> list[dict[str, Any]]:
    document = json.loads(payload)
    if document.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise InvalidDocumentError("Unsupported image manifest schema")
    return [dict(value) for value in document.get("images", [])]


def _set_stage(
    deps: PipelineDependencies,
    lease: JobLease,
    *,
    expected: IngestionStage,
    next_stage: IngestionStage,
    values: dict[str, object],
    book_status: BookStatus,
    book_values: dict[str, object] | None = None,
) -> JobSnapshot:
    with deps.sessions() as session:
        book = _lock_current_book(session, lease.job_uuid, lease.generation)
        record_artifacts(
            session,
            lease,
            expected_stage=expected,
            next_stage=next_stage,
            values=values,
            lease_seconds=settings.INGEST_LEASE_SECONDS,
        )
        snapshot = _snapshot(session, lease.job_uuid)
        book.status = book_status
        for key, value in (book_values or {}).items():
            setattr(book, key, value)
        session.commit()
        return snapshot


def _embed(fragments: list[Fragment]) -> list[EmbeddedChunk]:
    vectors = get_embedding_batch([fragment.text for fragment in fragments])
    if len(vectors) != len(fragments) or any(vector is None for vector in vectors):
        raise OSError(
            "Embedding provider did not return a complete deterministic batch"
        )
    return [
        EmbeddedChunk(
            chunk_index=fragment.chunk_index,
            fragment_path=fragment.path,
            text=fragment.text,
            vector=vector,
        )
        for fragment, vector in zip(fragments, vectors, strict=True)
        if vector is not None
    ]


def run_ingestion_pipeline(
    job_uuid: str,
    owner: str,
    *,
    dependencies: PipelineDependencies | None = None,
) -> dict[str, Any]:
    deps = dependencies or default_dependencies()
    with deps.sessions() as session:
        lease = claim_job(
            session,
            job_uuid,
            owner,
            lease_seconds=settings.INGEST_LEASE_SECONDS,
        )
        snapshot = _snapshot(session, job_uuid)
        session.commit()

    with (
        LeaseWatchdog(deps, lease) as watchdog,
        tempfile.TemporaryDirectory(prefix=f"grimoire-{job_uuid[:8]}-") as temp,
    ):
        workdir = Path(temp)
        source_path = workdir / f"source{snapshot.extension}"
        if snapshot.source_key and snapshot.source_key.startswith("/"):
            # Local filesystem path (inbox bypass): copy directly, skip object store
            import shutil
            shutil.copy2(snapshot.source_key, source_path)
        else:
            deps.store.download_to(snapshot.source_key, source_path)
        watchdog.check()
        if _file_hash(source_path) != snapshot.source_hash:
            raise InvalidDocumentError(
                "Downloaded source hash does not match accepted upload"
            )

        standalone_image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
        is_standalone_image = snapshot.extension in standalone_image_exts

        if is_standalone_image:
            # Fast path: skip text extraction/chunking, go straight to image embedding
            with source_path.open("rb") as f:
                image_bytes = f.read()
            image_sha256 = hashlib.sha256(image_bytes).hexdigest()
            ext = snapshot.extension.lstrip(".").lower()
            image_id = str(uuid.uuid4())

            width, height = 0, 0
            try:
                from PIL import Image as PIL_Image
                with PIL_Image.open(io.BytesIO(image_bytes)) as img:
                    width, height = img.size
            except Exception:
                pass

            extracted_images = [{
                "image_id": image_id,
                "image_bytes": image_bytes,
                "image_sha256": image_sha256,
                "ext": ext,
                "page_number": 0,
                "width": width,
                "height": height,
                "book_id": snapshot.book_uuid,
                "book_title": snapshot.book_title,
            }]

            watchdog.check()
            image_manifest_bytes, image_entries = _build_image_manifest(
                extracted_images,
                deps.store,
                book_uuid=snapshot.book_uuid,
                generation=lease.generation,
            )
            image_manifest_sha = hashlib.sha256(image_manifest_bytes).hexdigest()
            image_manifest_key = (
                f"jobs/{job_uuid}/g{lease.generation}/images-{image_manifest_sha}.json"
            )
            _upload_verified(
                deps.store,
                image_manifest_key,
                image_manifest_bytes,
                "application/json",
            )
            watchdog.check()
            snapshot = _set_stage(
                deps,
                lease,
                expected=snapshot.stage,
                next_stage=IngestionStage.IMAGES,
                values={
                    "image_manifest_key": image_manifest_key,
                    "image_manifest_sha256": image_manifest_sha,
                    "total_images": len(image_entries),
                },
                book_status=BookStatus.INDEXING,
                book_values={
                    "total_images": len(image_entries),
                    "indexed_images": 0,
                },
            )
            text_content = ""
            chunks = []
            fragments = []
            manifest_sha = ""
            indexed = 0
        else:
            # Normal text document pipeline
            if snapshot.stage == IngestionStage.SOURCE_READY:
                snapshot = _set_stage(
                    deps,
                    lease,
                    expected=IngestionStage.SOURCE_READY,
                    next_stage=IngestionStage.EXTRACTING,
                    values={},
                    book_status=BookStatus.EXTRACTING,
                )

            text_path = workdir / "extracted.txt"
            if snapshot.extracted_text_key and snapshot.extracted_text_sha256:
                text_bytes = _read_verified_artifact(
                    deps.store,
                    snapshot.extracted_text_key,
                    snapshot.extracted_text_sha256,
                    text_path,
                )
                text_content = text_bytes.decode("utf-8")
            else:
                if snapshot.stage != IngestionStage.EXTRACTING:
                    raise InvalidDocumentError(
                        "Extraction artifact missing after stage advancement"
                    )
                text_content = deps.extract(
                    source_path, snapshot.extension, workdir
                ).strip()
                watchdog.check()
                if len(text_content) < 10:
                    raise InvalidDocumentError("Extraction produced insufficient text")
                text_bytes = text_content.encode("utf-8")
                text_sha = hashlib.sha256(text_bytes).hexdigest()
                text_key = f"jobs/{job_uuid}/g{lease.generation}/extracted-{text_sha}.txt"
                _upload_verified(deps.store, text_key, text_bytes, "text/plain")
                watchdog.check()
                snapshot = _set_stage(
                    deps,
                    lease,
                    expected=IngestionStage.EXTRACTING,
                    next_stage=IngestionStage.CHUNKING,
                    values={
                        "extracted_text_key": text_key,
                        "extracted_text_sha256": text_sha,
                    },
                    book_status=BookStatus.CHUNKING,
                )

            manifest_path = workdir / "chunks.json"
            if snapshot.chunk_manifest_key and snapshot.chunk_manifest_sha256:
                manifest_bytes = _read_verified_artifact(
                    deps.store,
                    snapshot.chunk_manifest_key,
                    snapshot.chunk_manifest_sha256,
                    manifest_path,
                )
                chunks, fragments = _parse_manifest(manifest_bytes)
                manifest_sha = snapshot.chunk_manifest_sha256
            else:
                if snapshot.stage != IngestionStage.CHUNKING:
                    raise InvalidDocumentError(
                        "Chunk manifest missing after stage advancement"
                    )
                chunks = deps.chunk(text_content)
                if not chunks:
                    raise InvalidDocumentError("Chunking produced no canonical chunks")
                manifest_bytes, fragments = _build_manifest(chunks)
                manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
                manifest_key = (
                    f"jobs/{job_uuid}/g{lease.generation}/chunks-{manifest_sha}.json"
                )
                _upload_verified(
                    deps.store, manifest_key, manifest_bytes, "application/json"
                )
                watchdog.check()
                snapshot = _set_stage(
                    deps,
                    lease,
                    expected=IngestionStage.CHUNKING,
                    next_stage=IngestionStage.EMBEDDING,
                    values={
                        "chunk_manifest_key": manifest_key,
                        "chunk_manifest_sha256": manifest_sha,
                        "chunker_version": CHUNKER_VERSION,
                        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                        "total_chunks": len(chunks),
                    },
                    book_status=BookStatus.EMBEDDING,
                )

        indexed = snapshot.indexed_chunks
        batch_size = settings.INGEST_CHECKPOINT_BATCH_SIZE
        for start in range(snapshot.next_chunk_index, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            batch_fragments = [
                fragment
                for fragment in fragments
                if start <= fragment.chunk_index < end
            ]
            with deps.sessions() as session:
                renew_lease(
                    session,
                    lease,
                    lease_seconds=settings.INGEST_LEASE_SECONDS,
                )
                session.commit()
            vectors = deps.embed_vectors(
                [fragment.text for fragment in batch_fragments]
            )
            watchdog.check()
            if len(vectors) != len(batch_fragments) or any(
                vector is None for vector in vectors
            ):
                raise OSError(
                    "Embedding provider did not return a complete deterministic batch"
                )
            embedded = [
                EmbeddedChunk(
                    chunk_index=fragment.chunk_index,
                    fragment_path=fragment.path,
                    text=fragment.text,
                    vector=vector,
                )
                for fragment, vector in zip(batch_fragments, vectors, strict=True)
                if vector is not None
            ]
            indexed += deps.upsert(
                embedded,
                book_id=snapshot.book_uuid,
                generation=lease.generation,
                claim_epoch=lease.claim_epoch,
                job_uuid=job_uuid,
                manifest_sha256=manifest_sha,
                embedding_signature=snapshot.embedding_signature,
                book_title=snapshot.book_title,
                source_key=snapshot.source_key,
            )
            watchdog.check()
            with deps.sessions() as session:
                book = _lock_current_book(session, job_uuid, lease.generation)
                record_checkpoint(
                    session,
                    lease,
                    expected_chunk_index=start,
                    next_chunk_index=end,
                    indexed_chunks=indexed,
                )
                book.status = BookStatus.EMBEDDING
                book.total_chunks = len(chunks)
                book.indexed_chunks = indexed
                session.commit()

        image_manifest_path = workdir / "images.json"
        if snapshot.image_manifest_key and snapshot.image_manifest_sha256:
            image_manifest_bytes = _read_verified_artifact(
                deps.store,
                snapshot.image_manifest_key,
                snapshot.image_manifest_sha256,
                image_manifest_path,
            )
            image_entries = _parse_image_manifest(image_manifest_bytes)
            image_manifest_sha = snapshot.image_manifest_sha256
        else:
            if snapshot.stage != IngestionStage.EMBEDDING:
                raise InvalidDocumentError(
                    "Image manifest missing after stage advancement"
                )
            extracted_images = []
            standalone_image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
            
            # Extract images from PDFs
            if snapshot.extension == ".pdf":
                extracted_images = deps.extract_images(
                    str(source_path), snapshot.book_uuid, snapshot.book_title
                )
            
            # Handle standalone images - they become searchable images
            if snapshot.extension in standalone_image_exts:
                import uuid as uuid_module
                from PIL import Image as PIL_Image
                watchdog.check()
                with source_path.open("rb") as f:
                    image_bytes = f.read()
                image_sha256 = hashlib.sha256(image_bytes).hexdigest()
                ext = snapshot.extension.lstrip(".").lower()
                image_id = str(uuid_module.uuid4())
                
                # Get actual dimensions
                width, height = 0, 0
                try:
                    with PIL_Image.open(io.BytesIO(image_bytes)) as img:
                        width, height = img.size
                except Exception:
                    pass
                
                extracted_images = [
                    {
                        "image_id": image_id,
                        "image_bytes": image_bytes,
                        "image_sha256": image_sha256,
                        "ext": ext,
                        "page_number": 0,
                        "width": width,
                        "height": height,
                        "book_id": snapshot.book_uuid,
                        "book_title": snapshot.book_title,
                    }
                ]
            
            watchdog.check()
            image_manifest_bytes, image_entries = _build_image_manifest(
                extracted_images,
                deps.store,
                book_uuid=snapshot.book_uuid,
                generation=lease.generation,
            )
            image_manifest_sha = hashlib.sha256(image_manifest_bytes).hexdigest()
            image_manifest_key = (
                f"jobs/{job_uuid}/g{lease.generation}/images-{image_manifest_sha}.json"
            )
            _upload_verified(
                deps.store,
                image_manifest_key,
                image_manifest_bytes,
                "application/json",
            )
            watchdog.check()
            snapshot = _set_stage(
                deps,
                lease,
                expected=IngestionStage.EMBEDDING,
                next_stage=IngestionStage.IMAGES,
                values={
                    "image_manifest_key": image_manifest_key,
                    "image_manifest_sha256": image_manifest_sha,
                    "total_images": len(image_entries),
                },
                book_status=BookStatus.EMBEDDING,
                book_values={
                    "total_images": len(image_entries),
                    "indexed_images": 0,
                },
            )

        indexed_images = snapshot.indexed_images
        for image_index in range(snapshot.next_image_index, len(image_entries)):
            entry = image_entries[image_index]
            image_path = workdir / f"image-{image_index}.{entry['ext']}"
            image_bytes = _read_verified_artifact(
                deps.store,
                str(entry["object_key"]),
                str(entry["image_sha256"]),
                image_path,
            )
            vector = deps.embed_image(image_bytes)
            watchdog.check()
            if vector is None:
                raise OSError("Image embedding provider returned no vector")
            deps.index_image(
                {
                    **entry,
                    "book_id": snapshot.book_uuid,
                    "book_title": snapshot.book_title,
                    "image_bytes": image_bytes,
                },
                vector,
                generation=lease.generation,
                claim_epoch=lease.claim_epoch,
                image_index=image_index,
                job_uuid=job_uuid,
                manifest_sha256=image_manifest_sha,
                embedding_signature=snapshot.embedding_signature,
                store=deps.store,
            )
            watchdog.check()
            indexed_images += 1
            with deps.sessions() as session:
                book = _lock_current_book(session, job_uuid, lease.generation)
                record_image_checkpoint(
                    session,
                    lease,
                    expected_image_index=image_index,
                    next_image_index=image_index + 1,
                    indexed_images=indexed_images,
                )
                book.total_images = len(image_entries)
                book.indexed_images = indexed_images
                session.commit()

    with deps.sessions() as session:
        _lock_current_book(session, job_uuid, lease.generation)
        complete_job(session, lease)
        # Inline activation — skip outbox round-trip for speed
        from api.services.ingestion_activation import (
            reconcile_generation_activation,
            ActivationRejected,
        )
        try:
            result = reconcile_generation_activation(session, job_uuid)
            session.commit()
            activation_token = str(result["activation_token"])
            status = "indexed"
        except ActivationRejected as exc:
            logger.warning("Activation rejected for %s: %s — will retry via outbox", job_uuid, exc)
            session.rollback()
            create_event(session, job_uuid, "activate_generation")
            session.commit()
            activation_token = ""
            status = "awaiting_activation"
    return {
        "book_id": snapshot.book_uuid,
        "indexed_chunks": indexed,
        "indexed_images": indexed_images,
        "status": status,
        "activation_token": activation_token,
    }
