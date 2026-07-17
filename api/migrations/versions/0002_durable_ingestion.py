"""Introduce the durable, fenced ingestion ledger and dispatch outbox.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-16

Legacy ingestion_jobs rows contain transient Celery state and cannot be resumed.
The migration intentionally replaces that table while preserving every book and
its existing source-object reference.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE bookstatus ADD VALUE IF NOT EXISTS 'deleted'")
    op.alter_column(
        "books",
        "minio_object_key",
        new_column_name="source_object_key",
        existing_type=sa.String(length=1024),
        existing_nullable=True,
    )
    op.alter_column(
        "books",
        "uuid",
        existing_type=sa.String(length=36),
        type_=postgresql.UUID(as_uuid=False),
        postgresql_using="uuid::uuid",
        existing_nullable=False,
    )
    op.alter_column(
        "books",
        "file_size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.add_column(
        "books",
        sa.Column(
            "processing_generation", sa.Integer(), server_default="1", nullable=False
        ),
    )
    op.add_column("books", sa.Column("indexed_generation", sa.Integer(), nullable=True))
    op.add_column(
        "books", sa.Column("indexed_embedding_signature", sa.String(512), nullable=True)
    )
    op.add_column(
        "books", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "books",
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "books",
        sa.Column("total_images", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "books",
        sa.Column("indexed_images", sa.Integer(), server_default="0", nullable=False),
    )

    op.drop_table("ingestion_jobs")
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "book_id",
            sa.Integer(),
            sa.ForeignKey("books.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "running",
                "retry_wait",
                "succeeded",
                "failed",
                "cancel_requested",
                "cancelled",
                name="ingestionstate",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "source_ready",
                "extracting",
                "chunking",
                "embedding",
                "images",
                "finalizing",
                name="ingestionstage",
                native_enum=False,
            ),
            server_default="source_ready",
            nullable=False,
        ),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("claim_epoch", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="8", nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_chunk_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("indexed_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_images", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_image_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("indexed_images", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("extracted_text_key", sa.String(1024), nullable=True),
        sa.Column("extracted_text_sha256", sa.String(64), nullable=True),
        sa.Column("chunk_manifest_key", sa.String(1024), nullable=True),
        sa.Column("chunk_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("image_manifest_key", sa.String(1024), nullable=True),
        sa.Column("image_manifest_sha256", sa.String(64), nullable=True),
        sa.Column(
            "artifact_schema_version", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("chunker_version", sa.String(64), nullable=True),
        sa.Column("embedding_model", sa.String(256), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_signature", sa.String(512), nullable=True),
        sa.Column("progress_pct", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_error_class", sa.String(256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("generation >= 1", name="ck_ingestion_generation_positive"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_ingestion_attempt_bounds",
        ),
        sa.CheckConstraint(
            "next_chunk_index >= 0", name="ck_ingestion_checkpoint_nonnegative"
        ),
        sa.CheckConstraint("total_chunks >= 0", name="ck_ingestion_total_nonnegative"),
        sa.CheckConstraint(
            "indexed_chunks >= 0", name="ck_ingestion_indexed_nonnegative"
        ),
        sa.CheckConstraint(
            "next_chunk_index <= total_chunks",
            name="ck_ingestion_checkpoint_within_manifest",
        ),
        sa.CheckConstraint("total_images >= 0", name="ck_ingestion_images_nonnegative"),
        sa.CheckConstraint(
            "next_image_index >= 0 AND next_image_index <= total_images",
            name="ck_ingestion_image_checkpoint_within_manifest",
        ),
        sa.CheckConstraint(
            "indexed_images >= 0", name="ck_ingestion_indexed_images_nonnegative"
        ),
        sa.UniqueConstraint("uuid", name="uq_ingestion_jobs_uuid"),
        sa.UniqueConstraint(
            "book_id", "generation", name="uq_ingestion_job_book_generation"
        ),
    )
    op.create_index("ix_ingestion_jobs_uuid", "ingestion_jobs", ["uuid"], unique=True)
    op.create_index("ix_ingestion_jobs_book_id", "ingestion_jobs", ["book_id"])
    op.create_index(
        "ix_ingestion_jobs_celery_task_id", "ingestion_jobs", ["celery_task_id"]
    )
    op.create_index("ix_ingestion_jobs_lease_token", "ingestion_jobs", ["lease_token"])
    op.create_index(
        "ix_ingestion_recovery",
        "ingestion_jobs",
        ["state", "retry_at", "lease_expires_at"],
    )

    op.create_table(
        "ingestion_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "job_uuid",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("ingestion_jobs.uuid", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_uuid", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "publishing",
                "published",
                name="outboxstate",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("uuid", name="uq_ingestion_outbox_uuid"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_uuid",
            "event_type",
            name="uq_outbox_aggregate_event",
        ),
    )
    op.create_index(
        "ix_ingestion_outbox_uuid", "ingestion_outbox", ["uuid"], unique=True
    )
    op.create_index("ix_ingestion_outbox_job_uuid", "ingestion_outbox", ["job_uuid"])
    op.create_index(
        "ix_outbox_dispatch",
        "ingestion_outbox",
        ["state", "available_at", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_table("ingestion_outbox")
    op.drop_table("ingestion_jobs")

    book_status = postgresql.ENUM(
        "pending",
        "uploading",
        "extracting",
        "chunking",
        "embedding",
        "indexed",
        "failed",
        name="bookstatus",
        create_type=False,
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("book_id", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(36), nullable=False, unique=True),
        sa.Column("status", book_status, nullable=False, server_default="pending"),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ingestion_jobs_id", "ingestion_jobs", ["id"])
    op.create_index(
        "ix_ingestion_jobs_celery_task_id",
        "ingestion_jobs",
        ["celery_task_id"],
        unique=True,
    )

    op.drop_column("books", "indexed_images")
    op.drop_column("books", "total_images")
    op.drop_column("books", "row_version")
    op.drop_column("books", "deleted_at")
    op.drop_column("books", "indexed_embedding_signature")
    op.drop_column("books", "indexed_generation")
    op.drop_column("books", "processing_generation")
    op.alter_column(
        "books",
        "file_size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "books",
        "uuid",
        existing_type=postgresql.UUID(as_uuid=False),
        type_=sa.String(length=36),
        postgresql_using="uuid::text",
        existing_nullable=False,
    )
    op.alter_column(
        "books",
        "source_object_key",
        new_column_name="minio_object_key",
        existing_type=sa.String(length=1024),
        existing_nullable=True,
    )
