"""Celery application configuration and ingestion tasks."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from celery import Celery
from sqlalchemy.orm import Session

from api.config import settings
from api.models import Book, BookStatus, get_db
from api.services.image_svc import extract_images_from_pdf
from api.services.minio_svc import upload_object
from api.services.ollama_svc import (
    embed_with_recursive_bisection,
    get_embedding_batch,
)
from api.services.parser_svc import (
    chunk_text_semantic,
    compute_file_hash,
    convert_pdf_with_marker,
    extract_docx,
    extract_epub,
    extract_pdf_pymupdf,
    extract_text_file,
    sanitize_filename,
)
from api.services.qdrant_svc import upsert_chunks

logger = logging.getLogger(__name__)


def make_celery() -> Celery:
    app = Celery("grimoire")
    app.conf.update(
        broker_url=settings.REDIS_URL,
        result_backend=settings.REDIS_URL,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=7200,  # 2h per task
        worker_prefetch_multiplier=1,
        task_acks_late=True,
    )
    return app


celery_app = make_celery()


def _get_db_session() -> Session:
    """Get a DB session for Celery tasks (outside FastAPI context)."""
    return next(iter(get_db()))


@celery_app.task(bind=True, name="ingest.book")
def ingest_book_task(self: Any, book_path: str) -> dict[str, Any]:
    """Ingest a single book: parse -> chunk -> embed -> index + extract images."""
    db = _get_db_session()
    try:
        book_path_obj = Path(book_path)
        if not book_path_obj.exists():
            raise FileNotFoundError(f"Book not found: {book_path}")

        file_hash = compute_file_hash(book_path_obj)
        original_name = book_path_obj.name
        sanitized = sanitize_filename(book_path_obj.stem)
        ext = book_path_obj.suffix.lower()

        book = db.query(Book).filter(Book.file_hash == file_hash).first()
        if book is None:
            book_uuid = str(uuid.uuid4())
            book = Book(
                uuid=book_uuid,
                title=book_path_obj.stem.replace("_", " ").title(),
                original_filename=original_name,
                sanitized_filename=sanitized,
                file_extension=ext,
                file_size_bytes=book_path_obj.stat().st_size,
                file_hash=file_hash,
                status=BookStatus.EXTRACTING,
            )
            db.add(book)
            db.commit()
        else:
            book_uuid = book.uuid
            if book.status == BookStatus.INDEXED:
                return {
                    "book_id": book_uuid,
                    "indexed_chunks": book.indexed_chunks,
                    "status": "already_indexed",
                }
            book.status = BookStatus.EXTRACTING
            db.commit()

        self.update_state(
            state="STARTED",
            meta={"book_id": book_uuid, "status": "extracting", "progress": 5},
        )

        # ── Step 1: Parse to text ──────────────────────────
        text = ""
        output_dir = Path("/app/data/markdown")
        output_dir.mkdir(parents=True, exist_ok=True)

        if ext in (".pdf", ".PDF"):
            md_path = convert_pdf_with_marker(book_path_obj, output_dir)
            if md_path and md_path.exists():
                text = md_path.read_text(encoding="utf-8")
                book.markdown_path = str(md_path)
            else:
                text = extract_pdf_pymupdf(book_path_obj)
        elif ext in (".epub",):
            text = extract_epub(book_path_obj)
        elif ext in (".docx", ".doc"):
            text = extract_docx(book_path_obj)
        elif ext in (".txt", ".md", ".htm", ".html"):
            text = extract_text_file(book_path_obj)
        else:
            raise ValueError(f"Unsupported format: {ext}")

        if not text or len(text.strip()) < 50:
            raise ValueError(
                f"Extracted text is too short or empty for {original_name}"
            )

        # ── Step 1b: Extract images from PDFs ───────────────
        if ext in (".pdf", ".PDF"):
            logger.info(f"Extracting images from {original_name}...")
            images = extract_images_from_pdf(
                str(book_path_obj), book.uuid, book.title
            )
            if images:
                logger.info(f"Found {len(images)} images in {original_name}")

        book.status = BookStatus.CHUNKING
        db.commit()
        self.update_state(
            state="STARTED",
            meta={"book_id": book_uuid, "status": "chunking", "progress": 20},
        )

        # ── Step 2: Semantic chunking ──────────────────────
        chunks = chunk_text_semantic(text)
        book.total_chunks = len(chunks)
        db.commit()

        self.update_state(
            state="STARTED",
            meta={"book_id": book_uuid, "status": "embedding", "progress": 30},
        )

        # ── Step 3: Embed with batching + poison recovery ──
        batch_size = settings.EMBED_BATCH_SIZE
        total_batches = (len(chunks) + batch_size - 1) // batch_size
        total_indexed = 0

        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(chunks))
            batch_texts = chunks[start:end]

            embeddings = get_embedding_batch(batch_texts)

            successful_pairs = []
            for _i, (txt, emb) in enumerate(zip(batch_texts, embeddings, strict=False)):
                if emb is not None:
                    successful_pairs.append((txt, emb))
                else:
                    fragments = embed_with_recursive_bisection(txt)
                    frag_embs = get_embedding_batch(fragments)
                    for frag_text, frag_emb in zip(fragments, frag_embs, strict=False):
                        if frag_emb is not None:
                            successful_pairs.append((frag_text, frag_emb))

            if successful_pairs:
                upserted = upsert_chunks(
                    chunks=successful_pairs,
                    book_id=book_uuid,
                    book_title=book.title,
                    source_key=original_name,
                    page_number=batch_idx,
                )
                total_indexed += upserted

            progress = 30 + int(70 * (end / len(chunks)))
            book.indexed_chunks = total_indexed
            db.commit()
            self.update_state(
                state="STARTED",
                meta={
                    "book_id": book_uuid,
                    "status": "embedding",
                    "progress": progress,
                    "indexed_chunks": total_indexed,
                    "total_chunks": len(chunks),
                },
            )

            time.sleep(0.1)

        # ── Step 5: Upload to MinIO ────────────────────────
        object_key = f"{sanitized}{ext}"
        upload_object(object_key, book_path_obj.read_bytes())
        book.minio_object_key = object_key
        book.status = BookStatus.INDEXED
        db.commit()

        self.update_state(
            state="STARTED",
            meta={"book_id": book_uuid, "status": "indexed", "progress": 100},
        )

        print(f"Successfully indexed {original_name}: {total_indexed} chunks")
        return {
            "book_id": book_uuid,
            "indexed_chunks": total_indexed,
            "status": "indexed",
        }

    except Exception as e:
        error_msg = str(e)
        print(f"Ingestion FAILED for {book_path}: {error_msg}")
        try:
            if "book" in locals() and book is not None:
                book.status = BookStatus.FAILED
                book.error_message = error_msg[:2000]
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="ingest.batch")
def ingest_batch_task(self: Any, book_paths: list[str]) -> dict[str, Any]:
    """Ingest a batch of books — fire-and-forget, no blocking .get()."""
    total = len(book_paths)

    for i, path in enumerate(book_paths):
        ingest_book_task.delay(path)
        self.update_state(
            state="STARTED",
            meta={
                "status": "batch_queued",
                "progress": int((i + 1) / total * 100),
                "current": i + 1,
                "total": total,
            },
        )

    return {
        "total": total,
        "status": "all_queued",
        "message": f"Queued {total} books for ingestion. Track progress via /api/ingest/{{task_id}}/progress",
    }


# Register additional task modules (side-effect import)
from api.tasks import image_tasks as _image_tasks  # noqa: F401
