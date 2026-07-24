import json
from datetime import datetime, timedelta, timezone

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
from api.services.ingestion_cleanup import (
    cleanup_deleted_book,
    cleanup_stale_upload_reservations,
)


class Store:
    def __init__(self) -> None:
        book_uuid = "11111111-1111-1111-1111-111111111111"
        image_ids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(20)]
        image_keys = [
            f"books/{book_uuid}/generations/1/images/{image_id}.png"
            for image_id in image_ids
        ]
        self.image_keys = image_keys
        image_manifest = json.dumps(
            {
                "schema_version": 1,
                "images": [
                    {
                        "image_id": image_id,
                        "image_sha256": "a" * 64,
                        "object_key": key,
                        "ext": "png",
                        "page_number": index,
                        "width": 1,
                        "height": 1,
                    }
                    for index, (image_id, key) in enumerate(zip(image_ids, image_keys, strict=True))
                ],
            }
        ).encode()
        self.objects = {
            "source": b"source",
            "text": b"text",
            "chunks": b"chunks",
            "images": image_manifest,
            **{key: b"image" for key in image_keys},
            "books/11111111-1111-1111-1111-111111111111/generations/orphan.png": b"orphan",
        }
        self.deleted: list[str] = []

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def iter_bytes(self, key: str):
        yield self.objects[key]

    def iter_keys(self, prefix: str):
        yield from (key for key in self.objects if key.startswith(prefix))


def test_cleanup_is_external_then_finalizes_cancelled_job() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book = Book(
        uuid="11111111-1111-1111-1111-111111111111",
        title="Deleted",
        original_filename="deleted.txt",
        sanitized_filename="deleted.txt",
        file_extension=".txt",
        source_object_key="source",
        status=BookStatus.DELETED,
        deleted_at=datetime.now(timezone.utc),
    )
    session.add(book)
    session.flush()
    session.add(
        IngestionJob(
            uuid="22222222-2222-2222-2222-222222222222",
            book_id=book.id,
            state=IngestionState.CANCEL_REQUESTED,
            stage=IngestionStage.EMBEDDING,
            extracted_text_key="text",
            chunk_manifest_key="chunks",
            image_manifest_key="images",
        )
    )
    session.commit()
    store = Store()
    vector_calls: list[str] = []

    result = cleanup_deleted_book(
        session,
        book.uuid,
        store=store,
        delete_vectors=lambda uuid: vector_calls.append(uuid) or 3,
    )
    session.commit()

    job = session.query(IngestionJob).one()
    assert result["status"] == "cleaned"
    assert result["objects_deleted"] == 25
    assert job.state == IngestionState.CANCELLED
    assert store.objects == {}
    manifest_index = store.deleted.index("images")
    assert all(
        store.deleted.index(key) < manifest_index for key in store.image_keys
    )
    assert vector_calls == [book.uuid]


def test_stale_upload_reservation_cleans_post_upload_crash_orphan() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book = Book(
        uuid="33333333-3333-3333-3333-333333333333",
        title="Orphan",
        original_filename="orphan.txt",
        sanitized_filename="orphan.txt",
        file_extension=".txt",
        source_object_key="source",
        status=BookStatus.UPLOADING,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session.add(book)
    session.commit()
    store = Store()

    cleaned = cleanup_stale_upload_reservations(
        session, store=store, stale_seconds=3600
    )
    session.commit()

    assert cleaned == 1
    assert book.status == BookStatus.FAILED
    assert "source" not in store.objects
    assert session.query(IngestionJob).count() == 0
