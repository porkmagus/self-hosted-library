import hashlib
import json

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
from api.services.ingestion_activation import (
    ActivationRejected,
    derive_expected_vector_points,
    reconcile_generation_activation,
)


def _session(*, current_generation: int = 2) -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book = Book(
        uuid="11111111-1111-1111-1111-111111111111",
        title="Activation",
        original_filename="activation.txt",
        sanitized_filename="activation.txt",
        file_extension=".txt",
        status=BookStatus.EMBEDDING,
        processing_generation=current_generation,
    )
    session.add(book)
    session.flush()
    session.add(
        IngestionJob(
            uuid="22222222-2222-2222-2222-222222222222",
            book_id=book.id,
            generation=2,
            state=IngestionState.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            total_chunks=3,
            next_chunk_index=3,
            indexed_chunks=4,
            text_batch_claims=[{"start": 0, "end": 3, "claim_epoch": 5}],
            chunk_manifest_sha256="a" * 64,
            image_manifest_sha256="b" * 64,
            embedding_signature="model|dim=2|normalize=provider",
        )
    )
    session.commit()
    return session


def test_activation_updates_book_only_after_external_activation() -> None:
    session = _session()
    calls: list[dict] = []
    image_calls: list[dict] = []

    def activate(book_uuid: str, generation: int, **kwargs) -> None:
        calls.append({"book_uuid": book_uuid, "generation": generation, **kwargs})

    def activate_images(book_uuid: str, generation: int, **kwargs) -> None:
        image_calls.append({"book_uuid": book_uuid, "generation": generation, **kwargs})

    result = reconcile_generation_activation(
        session,
        "22222222-2222-2222-2222-222222222222",
        activate=activate,
        activate_images=activate_images,
        resolve_points=lambda _job, _book: (
            [{"id": str(index), "payload": {}} for index in range(4)],
            [],
        ),
    )
    session.commit()

    book = session.query(Book).one()
    assert result["status"] == "indexed"
    assert book.status == BookStatus.INDEXED
    assert book.indexed_generation == 2
    assert book.indexed_chunks == 4
    assert len(calls[0]["expected_points"]) == 4
    assert image_calls[0]["expected_points"] == []
    assert image_calls[0]["manifest_sha256"] == "b" * 64
    assert calls[0]["activation_token"] == image_calls[0]["activation_token"]
    assert book.indexed_activation_token == calls[0]["activation_token"]
    assert book.indexed_job_uuid == "22222222-2222-2222-2222-222222222222"
    assert book.activation_cleanup_pending is True


def test_superseded_generation_can_never_activate() -> None:
    session = _session(current_generation=3)
    called = False

    def activate(*args, **kwargs) -> None:
        nonlocal called
        called = True

    with pytest.raises(ActivationRejected, match="no longer current"):
        reconcile_generation_activation(
            session,
            "22222222-2222-2222-2222-222222222222",
            activate=activate,
            activate_images=activate,
        )
    assert called is False


def test_activation_derives_exact_ids_from_manifest_and_committed_claims() -> None:
    session = _session()
    job = session.query(IngestionJob).one()
    text_manifest = json.dumps(
        {
            "schema_version": 1,
            "fragments": [
                {"chunk_index": 0, "path": "root.L", "text": "a"},
                {"chunk_index": 0, "path": "root.R", "text": "b"},
                {"chunk_index": 1, "path": "root", "text": "c"},
                {"chunk_index": 2, "path": "root", "text": "d"},
            ],
        },
        separators=(",", ":"),
    ).encode()
    image_manifest = b'{"schema_version":1,"images":[]}'
    job.chunk_manifest_sha256 = hashlib.sha256(text_manifest).hexdigest()
    job.image_manifest_sha256 = hashlib.sha256(image_manifest).hexdigest()
    session.commit()

    text_points, image_points = derive_expected_vector_points(
        job,
        "11111111-1111-1111-1111-111111111111",
        text_manifest,
        image_manifest,
    )

    assert len({point["id"] for point in text_points}) == 4
    assert {point["payload"]["claim_epoch"] for point in text_points} == {5}
    assert image_points == []


@pytest.mark.parametrize("fail_images", [True, False])
def test_external_promotion_before_postgres_commit_keeps_old_authority(
    fail_images: bool,
) -> None:
    session = _session()
    book = session.query(Book).one()
    book.status = BookStatus.INDEXED
    book.indexed_generation = 1
    book.indexed_job_uuid = "33333333-3333-3333-3333-333333333333"
    book.indexed_activation_token = "old-token"
    session.commit()

    def activate_images(*args, **kwargs):
        if fail_images:
            raise OSError("image promotion failed")

    if fail_images:
        with pytest.raises(OSError, match="image promotion failed"):
            reconcile_generation_activation(
                session,
                "22222222-2222-2222-2222-222222222222",
                activate=lambda *args, **kwargs: None,
                activate_images=activate_images,
                resolve_points=lambda _job, _book: (
                    [{"id": str(index), "payload": {}} for index in range(4)],
                    [],
                ),
            )
    else:
        reconcile_generation_activation(
            session,
            "22222222-2222-2222-2222-222222222222",
            activate=lambda *args, **kwargs: None,
            activate_images=activate_images,
            resolve_points=lambda _job, _book: (
                [{"id": str(index), "payload": {}} for index in range(4)],
                [],
            ),
        )
    session.rollback()  # models either external failure or crash before DB commit
    book = session.query(Book).one()
    assert book.indexed_generation == 1
    assert book.indexed_job_uuid == "33333333-3333-3333-3333-333333333333"
    assert book.indexed_activation_token == "old-token"
