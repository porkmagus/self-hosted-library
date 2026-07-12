"""SQLAlchemy database models and initialization."""

from __future__ import annotations

import enum
import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy import Enum as SAEnum
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


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    author: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    sanitized_filename: Mapped[str] = mapped_column(String(150), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    minio_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[BookStatus] = mapped_column(
        SAEnum(BookStatus), default=BookStatus.PENDING, nullable=False
    )
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    celery_task_id: Mapped[str] = mapped_column(
        String(36), unique=True, index=True, nullable=False
    )
    status: Mapped[BookStatus] = mapped_column(
        SAEnum(BookStatus), default=BookStatus.PENDING, nullable=False
    )
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def init_db() -> None:
    """Create all tables. In production, use Alembic migrations."""
    Base.metadata.create_all(bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    """Dependency: yield a DB session."""
    with get_db_session() as db:
        yield db
