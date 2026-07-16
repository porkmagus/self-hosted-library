from sqlalchemy import BigInteger, inspect

from api.models import (
    Book,
    BookStatus,
    IngestionJob,
    IngestionOutbox,
    IngestionStage,
    IngestionState,
)


def test_book_status_enum_uses_database_values() -> None:
    status_type = Book.__table__.c.status.type
    assert status_type.enums == [status.value for status in BookStatus]


def test_book_tracks_processing_and_indexed_generations() -> None:
    columns = inspect(Book).columns
    required = {
        "uuid",
        "source_object_key",
        "file_hash",
        "file_size_bytes",
        "processing_generation",
        "indexed_generation",
        "indexed_embedding_signature",
        "indexed_activation_token",
        "indexed_job_uuid",
        "activation_cleanup_pending",
        "total_images",
        "indexed_images",
        "deleted_at",
        "row_version",
    }
    assert required <= set(columns.keys())
    assert isinstance(columns.file_size_bytes.type, BigInteger)
    assert "minio_object_key" not in columns


def test_ingestion_job_has_fencing_retry_and_artifact_fields() -> None:
    columns = inspect(IngestionJob).columns
    required = {
        "uuid",
        "book_id",
        "generation",
        "state",
        "stage",
        "lease_owner",
        "lease_token",
        "claim_epoch",
        "lease_expires_at",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "retry_at",
        "cancel_requested_at",
        "next_chunk_index",
        "total_chunks",
        "indexed_chunks",
        "source_hash",
        "extracted_text_key",
        "extracted_text_sha256",
        "chunk_manifest_key",
        "chunk_manifest_sha256",
        "image_manifest_key",
        "image_manifest_sha256",
        "total_images",
        "next_image_index",
        "indexed_images",
        "text_batch_claims",
        "image_batch_claims",
        "artifact_schema_version",
        "chunker_version",
        "embedding_signature",
        "last_error_class",
        "started_at",
        "finished_at",
    }
    assert required <= set(columns.keys())
    assert columns.uuid.unique is True
    assert columns.book_id.nullable is False
    assert columns.celery_task_id.nullable is True


def test_job_lifecycle_and_processing_stage_are_separate() -> None:
    assert [state.value for state in IngestionState] == [
        "pending",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
    ]
    assert [stage.value for stage in IngestionStage] == [
        "source_ready",
        "extracting",
        "chunking",
        "embedding",
        "images",
        "finalizing",
    ]
    assert IngestionJob.__table__.c.state.type.enums == [
        state.value for state in IngestionState
    ]
    assert IngestionJob.__table__.c.stage.type.enums == [
        stage.value for stage in IngestionStage
    ]


def test_dispatch_outbox_is_unique_per_job_and_event() -> None:
    columns = inspect(IngestionOutbox).columns
    assert {"job_uuid", "event_type", "state", "available_at", "claimed_at"} <= set(
        columns.keys()
    )
    constraints = {
        constraint.name for constraint in IngestionOutbox.__table__.constraints
    }
    assert "uq_outbox_aggregate_event" in constraints
