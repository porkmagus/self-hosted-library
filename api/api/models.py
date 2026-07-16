"""SQLAlchemy database models and initialization."""

from __future__ import annotations

import enum
import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from api.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all models."""


_engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, SessionLocal
    if _engine is None:
        _engine = __import__("sqlalchemy").create_engine(
            settings.DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10
        )
        SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
        logger.info("Database engine initialized")
    return _engine


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager: yield a DB session, closing it on exit."""
    if SessionLocal is None:
        get_engine()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BookStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    FAILED = "failed"
    DELETED = "deleted"


class IngestionState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class IngestionStage(str, enum.Enum):
    SOURCE_READY = "source_ready"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    IMAGES = "images"
    FINALIZING = "finalizing"


class OutboxState(str, enum.Enum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"


def _enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_class]


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), unique=True, index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    author: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    sanitized_filename: Mapped[str] = mapped_column(String(150), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    source_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    processing_generation: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    indexed_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indexed_embedding_signature: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    indexed_activation_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    indexed_job_uuid: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), nullable=True
    )
    activation_cleanup_pending: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    status: Mapped[BookStatus] = mapped_column(
        SAEnum(BookStatus, name="bookstatus", values_callable=_enum_values),
        default=BookStatus.PENDING,
        nullable=False,
    )
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_images: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexed_images: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def minio_object_key(self) -> str | None:
        """Temporary source compatibility while callers migrate to neutral naming."""
        return self.source_object_key

    @minio_object_key.setter
    def minio_object_key(self, value: str | None) -> None:
        self.source_object_key = value


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "generation", name="uq_ingestion_job_book_generation"
        ),
        CheckConstraint("generation >= 1", name="ck_ingestion_generation_positive"),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_ingestion_attempt_bounds",
        ),
        CheckConstraint(
            "next_chunk_index >= 0", name="ck_ingestion_checkpoint_nonnegative"
        ),
        CheckConstraint("total_chunks >= 0", name="ck_ingestion_total_nonnegative"),
        CheckConstraint("indexed_chunks >= 0", name="ck_ingestion_indexed_nonnegative"),
        CheckConstraint(
            "next_chunk_index <= total_chunks",
            name="ck_ingestion_checkpoint_within_manifest",
        ),
        CheckConstraint("total_images >= 0", name="ck_ingestion_images_nonnegative"),
        CheckConstraint(
            "next_image_index >= 0 AND next_image_index <= total_images",
            name="ck_ingestion_image_checkpoint_within_manifest",
        ),
        CheckConstraint(
            "indexed_images >= 0", name="ck_ingestion_indexed_images_nonnegative"
        ),
        Index(
            "ix_ingestion_recovery",
            "state",
            "retry_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), unique=True, index=True, nullable=False
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    state: Mapped[IngestionState] = mapped_column(
        SAEnum(
            IngestionState,
            name="ingestionstate",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=IngestionState.PENDING,
        nullable=False,
    )
    stage: Mapped[IngestionStage] = mapped_column(
        SAEnum(
            IngestionStage,
            name="ingestionstage",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=IngestionStage.SOURCE_READY,
        nullable=False,
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    claim_epoch: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=8, server_default="8", nullable=False
    )
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_chunk_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    indexed_chunks: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    total_images: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    next_image_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    indexed_images: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    text_batch_claims: Mapped[list[dict[str, int]]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    image_batch_claims: Mapped[list[dict[str, int]]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    source_hash: Mapped[str | None] = mapped_column(String(64))
    extracted_text_key: Mapped[str | None] = mapped_column(String(1024))
    extracted_text_sha256: Mapped[str | None] = mapped_column(String(64))
    chunk_manifest_key: Mapped[str | None] = mapped_column(String(1024))
    chunk_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    image_manifest_key: Mapped[str | None] = mapped_column(String(1024))
    image_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    chunker_version: Mapped[str | None] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(256))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    embedding_signature: Mapped[str | None] = mapped_column(String(512))
    progress_pct: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    last_error_class: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class IngestionOutbox(Base):
    __tablename__ = "ingestion_outbox"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_uuid",
            "event_type",
            name="uq_outbox_aggregate_event",
        ),
        Index("ix_outbox_dispatch", "state", "available_at", "claimed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), unique=True, index=True, nullable=False
    )
    job_uuid: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("ingestion_jobs.uuid", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_uuid: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[OutboxState] = mapped_column(
        SAEnum(
            OutboxState,
            name="outboxstate",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=OutboxState.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def init_db() -> None:
    """Run Alembic migrations to create/upgrade all tables."""
    import subprocess
    import sys
    from pathlib import Path

    api_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(api_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Alembic migration failed: %s", result.stderr)
        raise RuntimeError(f"Alembic migration failed: {result.stderr}")


def get_db() -> Generator[Session, None, None]:
    """Dependency: yield a DB session."""
    with get_db_session() as db:
        yield db
