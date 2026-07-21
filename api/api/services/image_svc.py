"""Image pipeline service - extract images from PDFs, embed with CLIP, index in Qdrant."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from typing import Any

from PIL import Image
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchAny,
    MatchValue,
    PayloadField,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.models import Book, BookStatus
from api.services.object_store import get_object_store
from api.services.qdrant_svc import get_qdrant_client

logger = logging.getLogger(__name__)
IMAGE_NAMESPACE = uuid.UUID("129d6e9a-5bb5-4db0-b550-14d8fc259876")

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
            "CLIP_MODEL_PATH", "openai/clip-vit-base-patch32"
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
    """Initialize or upgrade the generation-aware image collection."""
    client = get_qdrant_client()
    collection_name = settings.IMAGE_COLLECTION
    existing = {collection.name for collection in client.get_collections().collections}
    is_new = collection_name not in existing
    if is_new:
        client.create_collection(
            collection_name=collection_name,
            vectors_config={"image": VectorParams(size=512, distance=Distance.COSINE)},
        )
        schema: dict[str, Any] = {}
    else:
        info = client.get_collection(collection_name)
        schema = getattr(info, "payload_schema", {}) or {}

    desired = {
        "book_id": PayloadSchemaType.KEYWORD,
        "content_type": PayloadSchemaType.KEYWORD,
        "job_uuid": PayloadSchemaType.KEYWORD,
        "manifest_sha256": PayloadSchemaType.KEYWORD,
        "generation": PayloadSchemaType.INTEGER,
        "claim_epoch": PayloadSchemaType.INTEGER,
        "activation_token": PayloadSchemaType.KEYWORD,
        "visible": PayloadSchemaType.BOOL,
    }
    for field, field_schema in desired.items():
        if field not in schema:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=field_schema,
                wait=True,
            )
    if not is_new and "visible" not in schema:
        client.set_payload(
            collection_name=collection_name,
            payload={"visible": True},
            points=Filter(
                must=[IsEmptyCondition(is_empty=PayloadField(key="visible"))]
            ),
            wait=True,
        )
    if is_new:
        logger.info("Created %s collection", collection_name)


def normalize_image_bytes(image_bytes: bytes) -> tuple[bytes, str]:
    """Decode arbitrary embedded image data and deterministically encode PNG."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False)
    return output.getvalue(), "png"


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
                    image_bytes, image_ext = normalize_image_bytes(
                        bytes(base_image["image"])
                    )
                    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
                    image_id = str(
                        uuid.uuid5(
                            IMAGE_NAMESPACE,
                            f"{book_id}:{page_num}:{img_idx}:{image_sha256}",
                        )
                    )
                    extracted.append(
                        {
                            "image_id": image_id,
                            "image_bytes": image_bytes,
                            "image_sha256": image_sha256,
                            "ext": image_ext,
                            "page_number": page_num,
                            "width": img_info[2],
                            "height": img_info[3],
                            "book_id": book_id,
                            "book_title": book_title,
                        }
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to extract PDF image xref={xref} page={page_num}"
                    ) from exc
        doc.close()
        return extracted
    except Exception as e:
        logger.exception("Failed to extract images from %s: %s", pdf_path, e)
        raise


def build_image_point_id(
    book_id: str,
    generation: int,
    manifest_sha256: str,
    image_id: str,
    *,
    claim_epoch: int,
) -> str:
    return str(
        uuid.uuid5(
            IMAGE_NAMESPACE,
            f"vector:{book_id}:{generation}:{manifest_sha256}:claim:{claim_epoch}:{image_id}",
        )
    )


def index_image(
    image: dict[str, Any],
    embedding: list[float],
    *,
    generation: int,
    claim_epoch: int,
    image_index: int,
    job_uuid: str,
    manifest_sha256: str,
    embedding_signature: str,
    store: Any | None = None,
    qdrant: Any | None = None,
) -> str:
    """Persist one private image and stage its deterministic hidden vector."""
    object_store = store or get_object_store()
    vector_store = qdrant or get_qdrant_client()

    book_id = str(image["book_id"])
    book_title = str(image["book_title"])
    ext = str(image["ext"]).lower()
    image_id = str(image["image_id"])
    image_bytes = image["image_bytes"]
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise TypeError("image_bytes must be bytes")

    object_key = f"books/{book_id}/generations/{generation}/images/{image_id}.{ext}"
    payload_bytes = bytes(image_bytes)
    if not object_store.exists(object_key):
        object_store.upload_stream(
            object_key,
            io.BytesIO(payload_bytes),
            length=len(payload_bytes),
            content_type=f"image/{ext}",
        )
    if int(object_store.stat(object_key).size) != len(payload_bytes):
        raise OSError(f"Image object verification failed for {object_key}")

    point_id = build_image_point_id(
        book_id,
        generation,
        manifest_sha256,
        image_id,
        claim_epoch=claim_epoch,
    )
    point = PointStruct(
        id=point_id,
        vector={"image": embedding},
        payload={
            "content_type": "image",
            "book_id": book_id,
            "book_title": book_title,
            "image_id": image_id,
            "image_index": image_index,
            "object_key": object_key,
            "image_sha256": str(image["image_sha256"]),
            "ext": ext,
            "generation": generation,
            "claim_epoch": claim_epoch,
            "job_uuid": job_uuid,
            "manifest_sha256": manifest_sha256,
            "embedding_signature": embedding_signature,
            "visible": False,
            "page_number": int(image.get("page_number", 0) or 0),
            "width": int(image.get("width", 0) or 0),
            "height": int(image.get("height", 0) or 0),
        },
    )
    vector_store.upsert(
        collection_name=settings.IMAGE_COLLECTION,
        points=[point],
        wait=True,
    )
    return point_id


def activate_image_generation(
    book_id: str,
    generation: int,
    *,
    job_uuid: str,
    manifest_sha256: str,
    expected_points: list[dict[str, Any]],
    activation_token: str,
    qdrant: Any | None = None,
) -> None:
    """Verify exact accepted image IDs and stamp only that immutable set."""
    if not expected_points:
        return
    client = qdrant or get_qdrant_client()
    ids: list[int | str | uuid.UUID] = [str(point["id"]) for point in expected_points]
    records = client.retrieve(
        collection_name=settings.IMAGE_COLLECTION,
        ids=ids,
        with_payload=True,
        with_vectors=True,
    )
    actual = {str(record.id): record for record in records}
    if set(actual) != set(ids):
        raise OSError("Accepted image vector IDs are missing or unexpected")
    for expected in expected_points:
        record = actual[str(expected["id"])]
        payload = record.payload or {}
        if any(payload.get(key) != value for key, value in expected["payload"].items()):
            raise OSError(f"Image vector identity mismatch for {expected['id']}")
        if record.vector is None:
            raise OSError(f"Image vector is missing for {expected['id']}")
    for offset in range(0, len(ids), 512):
        client.set_payload(
            collection_name=settings.IMAGE_COLLECTION,
            payload={"visible": True, "activation_token": activation_token},
            points=PointIdsList(points=ids[offset : offset + 512]),
            wait=True,
        )


def retire_other_image_activations(book_id: str, activation_token: str) -> None:
    client = get_qdrant_client()
    client.set_payload(
        collection_name=settings.IMAGE_COLLECTION,
        payload={"visible": False},
        points=Filter(
            must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))],
            must_not=[
                FieldCondition(
                    key="activation_token", match=MatchValue(value=activation_token)
                )
            ],
        ),
        wait=True,
    )


def search_images(
    query: str,
    limit: int = 10,
    activation_tokens: list[str] | None = None,
    legacy_identities: list[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Search images using CLIP text embedding."""
    if activation_tokens == [] and not legacy_identities:
        return []
    text_embedding = get_text_embedding_for_search(query)
    if not text_embedding:
        return []
    must: list[Any] = [FieldCondition(key="visible", match=MatchValue(value=True))]
    should: list[Any] = []
    if activation_tokens:
        should.append(
            FieldCondition(
                key="activation_token", match=MatchAny(any=activation_tokens)
            )
        )
    for legacy_book_id, legacy_generation in legacy_identities or []:
        should.append(
            Filter(
                must=[
                    FieldCondition(
                        key="book_id", match=MatchValue(value=legacy_book_id)
                    ),
                    FieldCondition(
                        key="generation",
                        match=MatchValue(value=legacy_generation),
                    ),
                    IsEmptyCondition(is_empty=PayloadField(key="activation_token")),
                ]
            )
        )
    try:
        qdrant = get_qdrant_client()
        results = qdrant.query_points(
            collection_name=settings.IMAGE_COLLECTION,
            query=text_embedding,
            using="image",
            query_filter=Filter(must=must, should=should or None),
            score_threshold=settings.IMAGE_SEARCH_MIN_SCORE,
            limit=limit,
        )
        out: list[dict[str, Any]] = []
        for r in results.points:
            if float(r.score) < settings.IMAGE_SEARCH_MIN_SCORE:
                continue
            payload = r.payload or {}
            image_id = str(payload.get("image_id", "") or "")
            book_id = str(payload.get("book_id", "") or "")
            generation = int(payload.get("generation", 0) or 0)
            ext = str(payload.get("ext", "") or "")
            image_url = (
                f"/api/books/{book_id}/images/{generation}/{image_id}.{ext}"
                if book_id and generation > 0 and image_id and ext
                else ""
            )
            out.append(
                {
                    "image_id": image_id,
                    "book_id": book_id,
                    "book_title": payload.get("book_title", ""),
                    "generation": generation,
                    "job_uuid": payload.get("job_uuid"),
                    "activation_token": payload.get("activation_token"),
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


def filter_current_image_generations(
    session: Session, results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Use PostgreSQL's activated generation as final visibility authority."""
    book_ids = {str(result.get("book_id", "")) for result in results}
    if not book_ids:
        return []
    current = {
        str(book_uuid): (int(generation), job_uuid, activation_token)
        for book_uuid, generation, job_uuid, activation_token in session.execute(
            select(
                Book.uuid,
                Book.indexed_generation,
                Book.indexed_job_uuid,
                Book.indexed_activation_token,
            ).where(
                Book.uuid.in_(book_ids),
                Book.status == BookStatus.INDEXED,
                Book.deleted_at.is_(None),
                Book.indexed_generation.is_not(None),
            )
        )
        if generation is not None
    }
    return [
        result
        for result in results
        if (authority := current.get(str(result.get("book_id", "")))) is not None
        and authority[0] == int(result.get("generation", 0) or 0)
        and (
            authority[1] is None
            or str(authority[1]) == str(result.get("job_uuid", "") or "")
        )
        and (
            authority[2] is None
            or authority[2] == str(result.get("activation_token", "") or "")
        )
    ]
