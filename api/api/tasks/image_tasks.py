"""Image indexing Celery tasks — thin wrappers around image_svc."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from api.services.image_svc import (
    get_image_embedding,
    index_image,
    init_image_collection,
    process_images_for_book,
)
from api.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="index_pdf_images")
def index_pdf_images_task(pdf_path: str, book_id: str, book_title: str) -> int:
    """Extract and index images from a single PDF."""
    try:
        return process_images_for_book(pdf_path, book_id, book_title)
    except Exception as e:
        logger.error("Failed to index images from %s: %s", pdf_path, e)
        return 0


@celery_app.task(name="index_all_images")
def index_all_images_task(base_dir: str = "/app/data/inbox") -> dict[str, Any]:
    """Scan a directory tree for images and index them with CLIP."""
    import uuid

    root = Path(base_dir).expanduser()
    if not root.exists():
        logger.warning("Image scan path missing: %s", root)
        return {"indexed": 0, "failed": 0, "error": f"missing path {root}"}

    init_image_collection()

    exts = {".png", ".jpg", ".jpeg", ".gif", ".tiff", ".webp"}
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    logger.info("Found %d images under %s", len(files), root)

    indexed = 0
    failed = 0
    for img_path in files:
        try:
            image_bytes = img_path.read_bytes()
            if len(image_bytes) < 5_000:
                continue
            embedding = get_image_embedding(image_bytes)
            if not embedding:
                failed += 1
                continue
            image_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(img_path)))
            book_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(img_path.parent)))
            book_title = img_path.parent.name or "Unknown"
            ok = index_image(
                {
                    "image_id": image_id,
                    "book_id": book_id,
                    "book_title": book_title,
                    "page_number": 0,
                    "image_bytes": image_bytes,
                    "ext": img_path.suffix.lstrip(".").lower() or "jpg",
                    "width": 0,
                    "height": 0,
                },
                embedding,
            )
            if ok:
                indexed += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("Failed to index %s: %s", img_path, e)
            failed += 1

    result: dict[str, Any] = {"indexed": indexed, "failed": failed}
    logger.info("Image indexing complete: %s", result)
    return result
