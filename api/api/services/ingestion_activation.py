"""Serialized activation of fully materialized Qdrant generations."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.models import Book, BookStatus, IngestionJob, IngestionState
from api.services.image_svc import (
    activate_image_generation,
    build_image_point_id,
    parse_persisted_image_manifest,
)
from api.services.object_store import get_object_store
from api.services.qdrant_svc import (
    activate_generation,
    build_chunk_point_id,
)
from api.services.safe_artifacts import read_bounded_regular_file


class ActivationRejected(RuntimeError):
    pass


def _validate_batch_claims(claims: list[dict[str, int]], total: int) -> None:
    cursor = 0
    for claim in claims:
        if (
            int(claim.get("start", -1)) != cursor
            or int(claim.get("end", -1)) <= cursor
            or int(claim.get("claim_epoch", 0)) <= 0
        ):
            raise ActivationRejected("Committed vector batch ownership is inconsistent")
        cursor = int(claim["end"])
    if cursor != total:
        raise ActivationRejected("Committed vector batches do not cover the manifest")


def _claim_epoch(index: int, claims: list[dict[str, int]]) -> int:
    owners = [
        int(claim["claim_epoch"])
        for claim in claims
        if int(claim["start"]) <= index < int(claim["end"])
    ]
    if len(owners) != 1:
        raise ActivationRejected(f"Logical item {index} has {len(owners)} owners")
    return owners[0]


def derive_expected_vector_points(
    job: IngestionJob,
    book_uuid: str,
    text_manifest: bytes,
    image_manifest: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive the exact immutable Qdrant set from manifests and accepted claims."""
    if hashlib.sha256(text_manifest).hexdigest() != job.chunk_manifest_sha256:
        raise ActivationRejected("Text manifest checksum mismatch")
    if hashlib.sha256(image_manifest).hexdigest() != job.image_manifest_sha256:
        raise ActivationRejected("Image manifest checksum mismatch")
    text_document = json.loads(text_manifest)
    try:
        images = parse_persisted_image_manifest(
            image_manifest, book_id=book_uuid, generation=job.generation
        )
    except ValueError as exc:
        raise ActivationRejected("Invalid image manifest") from exc
    if text_document.get("schema_version") != 1:
        raise ActivationRejected("Unsupported vector manifest schema")
    text_claims = list(job.text_batch_claims or [])
    image_claims = list(job.image_batch_claims or [])
    _validate_batch_claims(text_claims, job.total_chunks)
    _validate_batch_claims(image_claims, job.total_images)

    fragments = list(text_document.get("fragments", []))
    fragment_identities = [
        (int(fragment["chunk_index"]), str(fragment["path"])) for fragment in fragments
    ]
    if len(set(fragment_identities)) != len(fragment_identities):
        raise ActivationRejected("Text manifest contains duplicate fragment identities")
    if {identity[0] for identity in fragment_identities} != set(
        range(job.total_chunks)
    ):
        raise ActivationRejected("Text manifest logical chunk coverage is incomplete")
    text_points: list[dict[str, Any]] = []
    for fragment in fragments:
        chunk_index = int(fragment["chunk_index"])
        fragment_path = str(fragment["path"])
        epoch = _claim_epoch(chunk_index, text_claims)
        text_points.append(
            {
                "id": build_chunk_point_id(
                    book_uuid,
                    job.generation,
                    str(job.chunk_manifest_sha256),
                    chunk_index,
                    fragment_path,
                    epoch,
                ),
                "payload": {
                    "book_id": book_uuid,
                    "job_uuid": job.uuid,
                    "generation": job.generation,
                    "manifest_sha256": job.chunk_manifest_sha256,
                    "claim_epoch": epoch,
                    "chunk_index": chunk_index,
                    "fragment_path": fragment_path,
                    "embedding_signature": job.embedding_signature,
                },
            }
        )

    image_points: list[dict[str, Any]] = []

    if len(images) != job.total_images or len(
        {str(image["image_id"]) for image in images}
    ) != len(images):
        raise ActivationRejected(
            "Image manifest identities are incomplete or duplicated"
        )
    for image_index, image in enumerate(images):
        epoch = _claim_epoch(image_index, image_claims)
        image_id = str(image["image_id"])
        image_points.append(
            {
                "id": build_image_point_id(
                    book_uuid,
                    job.generation,
                    str(job.image_manifest_sha256),
                    image_id,
                    claim_epoch=epoch,
                ),
                "payload": {
                    "book_id": book_uuid,
                    "job_uuid": job.uuid,
                    "generation": job.generation,
                    "manifest_sha256": job.image_manifest_sha256,
                    "claim_epoch": epoch,
                    "image_index": image_index,
                    "image_id": image_id,
                    "embedding_signature": job.embedding_signature,
                },
            }
        )
    if (
        len(text_points) != job.indexed_chunks
        or len(image_points) != job.indexed_images
    ):
        raise ActivationRejected(
            "Derived vector set does not match committed point counts"
        )
    return text_points, image_points


def _read_artifact(key: str, *, max_bytes: int) -> bytes:
    store = get_object_store()
    with tempfile.TemporaryDirectory(prefix="activation-") as directory:
        destination = Path(directory) / "artifact"
        store.download_to(key, destination)
        return read_bounded_regular_file(destination, max_bytes=max_bytes)


def _resolve_job_points(
    job: IngestionJob, book_uuid: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not job.chunk_manifest_key or not job.image_manifest_key:
        raise ActivationRejected("Job manifest object keys are incomplete")
    return derive_expected_vector_points(
        job,
        book_uuid,
        _read_artifact(job.chunk_manifest_key, max_bytes=67_108_864),
        _read_artifact(job.image_manifest_key, max_bytes=4_000_000),
    )


def reconcile_generation_activation(
    session: Session,
    job_uuid: str,
    *,
    activate: Callable[..., None] = activate_generation,
    activate_images: Callable[..., None] = activate_image_generation,
    resolve_points: Callable[
        [IngestionJob, str], tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ] = _resolve_job_points,
) -> dict[str, int | str]:
    """Activate only the generation PostgreSQL still declares current."""
    initial = session.execute(
        select(IngestionJob).where(IngestionJob.uuid == job_uuid)
    ).scalar_one_or_none()
    if initial is None:
        raise ActivationRejected("Ingestion job does not exist")
    book = session.get(Book, initial.book_id)
    if book is None:
        raise ActivationRejected("Book does not exist")

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:book_uuid))"),
            {"book_uuid": book.uuid},
        )

    book = session.execute(
        select(Book).where(Book.id == initial.book_id).with_for_update()
    ).scalar_one()
    job = session.execute(
        select(IngestionJob).where(IngestionJob.uuid == job_uuid).with_for_update()
    ).scalar_one()

    if job.state != IngestionState.SUCCEEDED:
        raise ActivationRejected(f"Job is not succeeded: {job.state.value}")
    if book.deleted_at is not None:
        raise ActivationRejected("Book is deleted")
    if book.processing_generation != job.generation:
        raise ActivationRejected("Job generation is no longer current")
    if (
        not job.chunk_manifest_sha256
        or not job.image_manifest_sha256
        or not job.embedding_signature
    ):
        raise ActivationRejected("Job activation identity is incomplete")
    text_claims = list(job.text_batch_claims or [])
    image_claims = list(job.image_batch_claims or [])
    _validate_batch_claims(text_claims, job.total_chunks)
    _validate_batch_claims(image_claims, job.total_images)
    activation_token = hashlib.sha256(
        json.dumps(
            {
                "job_uuid": job.uuid,
                "generation": job.generation,
                "text_manifest": job.chunk_manifest_sha256,
                "image_manifest": job.image_manifest_sha256,
                "text_batches": text_claims,
                "image_batches": image_claims,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    text_points, image_points = resolve_points(job, book.uuid)

    # The advisory lock spans Qdrant verification/promotion and the DB commit.
    # A crash leaves the operation safely retryable and never exposes a partial set.
    activate(
        book.uuid,
        job.generation,
        job_uuid=job.uuid,
        manifest_sha256=job.chunk_manifest_sha256,
        expected_points=text_points,
        activation_token=activation_token,
    )
    activate_images(
        book.uuid,
        job.generation,
        job_uuid=job.uuid,
        manifest_sha256=job.image_manifest_sha256,
        expected_points=image_points,
        activation_token=activation_token,
    )
    book.indexed_generation = job.generation
    book.indexed_embedding_signature = job.embedding_signature
    book.indexed_activation_token = activation_token
    book.indexed_job_uuid = job.uuid
    book.activation_cleanup_pending = True
    book.status = BookStatus.INDEXED
    book.indexed_chunks = job.indexed_chunks
    book.total_chunks = job.total_chunks
    book.indexed_images = job.indexed_images
    book.total_images = job.total_images
    book.error_message = None
    session.flush()
    return {
        "book_id": book.uuid,
        "generation": job.generation,
        "indexed_chunks": job.indexed_chunks,
        "indexed_images": job.indexed_images,
        "activation_token": activation_token,
        "status": "indexed",
    }
