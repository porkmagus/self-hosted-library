"""Image pipeline service - extract images from PDFs, embed with CLIP, index in Qdrant."""

from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Any

from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

from api.config import settings
from api.services.minio_svc import build_public_object_url, get_minio_client
from api.services.qdrant_svc import get_qdrant_client

logger = logging.getLogger(__name__)

_CLIP_MODEL: Any | None = None
_CLIP_PROCESSOR: Any | None = None


def _load_clip() -> tuple[Any | None, Any | None]:
    """Load CLIP model (singleton)."""
    global _CLIP_MODEL, _CLIP_PROCESSOR
    if _CLIP_MODEL is not None:
        return _CLIP_MODEL, _CLIP_PROCESSOR
    try:
        from transformers import CLIPModel, CLIPProcessor

        model_path = os.environ.get(
            "CLIP_MODEL_PATH", "/app/models/clip-vit-base-patch32"
        )
        logger.info("Loading CLIP model from %s", model_path)
        _CLIP_MODEL = CLIPModel.from_pretrained(model_path)
        _CLIP_PROCESSOR = CLIPProcessor.from_pretrained(model_path)
        _CLIP_MODEL.eval()  # type: ignore[no-untyped-call]
        logger.info("CLIP model loaded")
        return _CLIP_MODEL, _CLIP_PROCESSOR
    except Exception as e:
        logger.warning("Failed to load CLIP model: %s", e)
        return None, None


def get_image_embedding(image_bytes: bytes) -> list[float] | None:
    """Get CLIP image embedding (512-dim) from image bytes."""
    model, processor = _load_clip()
    if model is None or processor is None:
        return None
    try:
        import torch
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = processor(images=[img], return_tensors="pt")
        with torch.no_grad():
            vision_output = model.vision_model(**inputs)
            cls_token = vision_output.pooler_output
            projected = model.visual_projection(cls_token)
            normalized = projected / projected.norm(dim=-1, keepdim=True)
            return list(normalized[0].tolist())
    except Exception as e:
        logger.error("Failed to get image embedding: %s", e)
        return None


def get_text_embedding_for_search(query: str) -> list[float] | None:
    """Get CLIP text embedding (512-dim) for search query."""
    model, processor = _load_clip()
    if model is None or processor is None:
        return None
    try:
        import torch

        inputs = processor(text=[query], return_tensors="pt", padding=True)
        with torch.no_grad():
            text_output = model.text_model(**inputs)
            cls_token = text_output.pooler_output
            projected = model.text_projection(cls_token)
            normalized = projected / projected.norm(dim=-1, keepdim=True)
            return list(normalized[0].tolist())
    except Exception as e:
        logger.error("Failed to get text embedding: %s", e)
        return None


def init_image_collection() -> None:
    """Initialize Qdrant image collection."""
    client = get_qdrant_client()
    collection_name = settings.IMAGE_COLLECTION
    collections = client.get_collections().collections
    if collection_name not in [c.name for c in collections]:
        client.create_collection(
            collection_name=collection_name,
            vectors_config={"image": VectorParams(size=512, distance=Distance.COSINE)},
        )
        client.create_payload_index(
            collection_name, "book_id", PayloadSchemaType.KEYWORD
        )
        client.create_payload_index(
            collection_name, "content_type", PayloadSchemaType.KEYWORD
        )
        logger.info("Created %s collection", collection_name)


def extract_images_from_pdf(
    pdf_path: str, book_id: str, book_title: str
) -> list[dict[str, Any]]:
    """Extract images from a PDF."""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        extracted: list[dict[str, Any]] = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            images = page.get_images(full=True)
            for img_idx, img_info in enumerate(images):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue
                    image_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS, f"{pdf_path}:{page_num}:{img_idx}"
                        )
                    )
                    extracted.append(
                        {
                            "image_id": image_id,
                            "image_bytes": base_image["image"],
                            "ext": base_image["ext"],
                            "page_number": page_num,
                            "width": img_info[2],
                            "height": img_info[3],
                            "book_id": book_id,
                            "book_title": book_title,
                        }
                    )
                except Exception:
                    pass
        doc.close()
        return extracted
    except Exception as e:
        logger.error("Failed to extract images from %s: %s", pdf_path, e)
        return []


def index_image(image: dict[str, Any], embedding: list[float]) -> bool:
    """Index a single image in MinIO + Qdrant."""
    try:
        minio = get_minio_client()
        qdrant = get_qdrant_client()

        book_id = str(image["book_id"])
        book_title = str(image["book_title"])
        ext = str(image["ext"])
        image_id = str(image["image_id"])
        image_bytes = image["image_bytes"]
        if not isinstance(image_bytes, (bytes, bytearray)):
            return False

        filename = f"images/{book_id}/{image_id}.{ext}"
        minio.put_object(
            settings.MINIO_BUCKET,
            filename,
            io.BytesIO(bytes(image_bytes)),
            length=len(image_bytes),
            content_type=f"image/{ext}",
        )

        point = PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, image_id)),
            vector={"image": embedding},
            payload={
                "content_type": "image",
                "book_id": book_id,
                "book_title": book_title,
                "image_id": image_id,
                "minio_key": filename,
                "page_number": int(image.get("page_number", 0) or 0),
                "width": int(image.get("width", 0) or 0),
                "height": int(image.get("height", 0) or 0),
            },
        )
        qdrant.upsert(collection_name=settings.IMAGE_COLLECTION, points=[point])
        return True
    except Exception as e:
        logger.error("Failed to index image: %s", e)
        return False


def search_images(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search images using CLIP text embedding."""
    text_embedding = get_text_embedding_for_search(query)
    if not text_embedding:
        return []
    try:
        qdrant = get_qdrant_client()
        results = qdrant.query_points(
            collection_name=settings.IMAGE_COLLECTION,
            query=text_embedding,
            using="image",
            limit=limit,
        )
        out: list[dict[str, Any]] = []
        for r in results.points:
            payload = r.payload or {}
            minio_key = str(payload.get("minio_key", "") or "")
            image_url = (
                build_public_object_url(minio_key)
                if minio_key
                else str(payload.get("image_url", "") or "")
            )
            out.append(
                {
                    "image_id": payload.get("image_id", ""),
                    "book_id": payload.get("book_id", ""),
                    "book_title": payload.get("book_title", ""),
                    "image_url": image_url,
                    "page_number": payload.get("page_number", 0),
                    "score": r.score,
                    "content_type": "image",
                }
            )
        return out
    except Exception as e:
        logger.error("Image search failed: %s", e)
        return []


def process_images_for_book(pdf_path: str, book_id: str, book_title: str) -> int:
    """Process all images from a PDF."""
    images = extract_images_from_pdf(pdf_path, book_id, book_title)
    indexed = 0
    for img in images:
        emb = get_image_embedding(img["image_bytes"])
        if emb and index_image(img, emb):
            indexed += 1
    return indexed
