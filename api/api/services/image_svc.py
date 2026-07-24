"""Image pipeline service - extract images from PDFs, embed with CLIP, index in Qdrant."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
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
from api.services.pdf_limits import TERMINAL_DOCUMENT_EXIT_CODE, DocumentLimitError
from api.services.qdrant_svc import get_qdrant_client
from api.services.safe_artifacts import read_bounded_regular_file

logger = logging.getLogger(__name__)
IMAGE_NAMESPACE = uuid.UUID("129d6e9a-5bb5-4db0-b550-14d8fc259876")
PERSISTED_IMAGE_FIELDS = {
    "image_id", "image_sha256", "object_key", "ext",
    "page_number", "width", "height",
}
PERSISTED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}

_CLIP_MODEL: Any | None = None
_CLIP_PROCESSOR: Any | None = None


class EmbeddedImageDecodeError(ValueError):
    """One embedded raster is unreadable; surrounding extraction remains valid."""


class ImageDocumentError(ValueError):
    """The PDF is malformed or violates a deterministic document limit."""


def validate_image_collection_schema(collection_name: str, info: Any) -> None:
    """Refuse to run against a collection that cannot accept CLIP vectors."""
    vectors = info.config.params.vectors
    image_vector = vectors.get("image") if isinstance(vectors, dict) else None
    if image_vector is None or int(image_vector.size) != 512:
        raise RuntimeError(
            f"{collection_name} must use a named 512-dimensional 'image' vector; "
            f"found {vectors!r}. Recreate the collection before ingestion."
        )


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
    if settings.EMBEDDING_SERVER_URL:
        try:
            from api.services.embedding_client import get_clip_image_embedding

            return get_clip_image_embedding(image_bytes)
        except Exception as e:
            logger.error("Remote CLIP embedding failed: %s", e)
            return None
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
        validate_image_collection_schema(collection_name, info)
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


def normalize_image_bytes(
    image_bytes: bytes, *, max_pixels: int | None = None
) -> tuple[bytes, str]:
    """Decode arbitrary embedded image data and deterministically encode PNG."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if max_pixels is not None and image.width * image.height > max_pixels:
                raise Image.DecompressionBombError("Decoded image pixel limit exceeded")
            image.load()
            if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=False)
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        SyntaxError,
        OSError,
    ) as exc:
        raise EmbeddedImageDecodeError("Embedded image decode failed") from exc
    return output.getvalue(), "png"


def _extract_images_in_process(
    pdf_path: str,
    book_id: str,
    book_title: str,
    *,
    include_full_page: bool = False,
) -> list[dict[str, Any]]:
    """Extract displayed images. Call only inside the bounded PDF subprocess."""
    import fitz

    from api.services.pdf_limits import (
        DEFAULT_MAX_OUTPUT_BYTES,
        DEFAULT_MAX_PAGE_PIXELS,
        DEFAULT_MAX_PAGES,
        DEFAULT_PDF_DPI,
        positive_env,
        validate_document,
    )

    max_images = positive_env("PDF_IMAGE_MAX_IMAGES", 5_000)
    max_image_bytes = positive_env("PDF_IMAGE_MAX_IMAGE_BYTES", 16_777_216)
    max_output_bytes = positive_env("PDF_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)
    max_image_pixels = positive_env("PDF_IMAGE_MAX_PIXELS", 25_000_000)
    document = fitz.open(pdf_path)
    extracted: list[dict[str, Any]] = []
    total_references = skipped_corrupt = skipped_full_page = skipped_tiny = 0
    output_bytes = full_page_kept = 0
    try:
        validate_document(
            document,
            dpi=positive_env("PDF_DPI", DEFAULT_PDF_DPI),
            max_pages=positive_env("PDF_MAX_PAGES", DEFAULT_MAX_PAGES),
            max_page_pixels=positive_env(
                "PDF_MAX_PAGE_PIXELS", DEFAULT_MAX_PAGE_PIXELS
            ),
        )
        for page_num in range(len(document)):
            page = document.load_page(page_num)
            images = page.get_image_info(hashes=True, xrefs=True)
            for img_idx, img_info in enumerate(images):
                total_references += 1
                if total_references > max_images:
                    raise DocumentLimitError("PDF displayed-image count limit exceeded")
                width = int(img_info.get("width", 0) or 0)
                height = int(img_info.get("height", 0) or 0)
                pixels = width * height
                if pixels > max_image_pixels:
                    raise DocumentLimitError(
                        "PDF displayed-image pixel limit exceeded: "
                        f"page={page_num + 1} image={img_idx + 1} "
                        f"pixels={pixels} limit={max_image_pixels}"
                    )
                if min(width, height) < 32 or pixels < 4096:
                    skipped_tiny += 1
                    continue
                page_area = page.rect.get_area()
                bbox = img_info.get("bbox")
                if hasattr(bbox, "get_area"):
                    image_area = bbox.get_area()
                elif bbox and len(bbox) == 4:
                    x0, y0, x1, y1 = bbox
                    image_area = max(0.0, float(x1 - x0)) * max(
                        0.0, float(y1 - y0)
                    )
                else:
                    image_area = 0.0
                is_full_page = page_area > 0 and image_area / page_area >= 0.90
                if is_full_page and (not include_full_page or full_page_kept >= 1):
                    skipped_full_page += 1
                    continue
                xref = int(img_info.get("xref", 0) or 0)
                if xref <= 0:
                    skipped_corrupt += 1
                    continue
                try:
                    raw_image = document.extract_image(xref)["image"]
                    if len(raw_image) > max_image_bytes:
                        raise DocumentLimitError("PDF image byte limit exceeded")
                    image_bytes, image_ext = normalize_image_bytes(
                        bytes(raw_image), max_pixels=max_image_pixels
                    )
                    if len(image_bytes) > max_image_bytes:
                        raise DocumentLimitError(
                            "Normalized PDF image byte limit exceeded"
                        )
                except EmbeddedImageDecodeError as exc:
                    skipped_corrupt += 1
                    logger.debug(
                        "Skipping unreadable PDF image index=%s page=%s in %s: %s",
                        img_idx,
                        page_num,
                        pdf_path,
                        exc,
                    )
                    continue
                output_bytes += len(image_bytes)
                if output_bytes > max_output_bytes:
                    raise DocumentLimitError("PDF image cumulative output limit exceeded")
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
                        "width": width,
                        "height": height,
                        "book_id": book_id,
                        "book_title": book_title,
                    }
                )
                if is_full_page:
                    full_page_kept += 1
        if skipped_corrupt or skipped_full_page or skipped_tiny:
            logger.warning(
                "PDF image extraction partial for %s: total=%s extracted=%s "
                "skipped_corrupt=%s skipped_full_page=%s skipped_tiny=%s",
                pdf_path,
                total_references,
                len(extracted),
                skipped_corrupt,
                skipped_full_page,
                skipped_tiny,
            )
        return extracted
    finally:
        document.close()


def _validate_pdf_image_manifest(
    metadata: object, *, book_id: str, book_title: str
) -> list[dict[str, Any]]:
    """Validate every child-controlled field before it reaches storage keys."""
    if not isinstance(metadata, list) or len(metadata) > 5_000:
        raise RuntimeError("Invalid PDF image manifest")
    expected_fields = {
        "filename",
        "image_id",
        "image_sha256",
        "ext",
        "page_number",
        "width",
        "height",
        "book_id",
        "book_title",
    }
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(metadata):
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise RuntimeError("Invalid PDF image manifest schema")
        item = dict(value)
        filename = item["filename"]
        image_id = item["image_id"]
        digest = item["image_sha256"]
        page_number = item["page_number"]
        width = item["width"]
        height = item["height"]
        try:
            parsed_id = str(uuid.UUID(str(image_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Invalid PDF image manifest image ID") from exc
        if (
            filename != f"image-{index:05d}.png"
            or parsed_id != image_id
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or item["ext"] != "png"
            or type(page_number) is not int
            or not 0 <= page_number < 2_000
            or type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
            or width * height > 25_000_000
            or item["book_id"] != book_id
            or item["book_title"] != book_title
        ):
            raise RuntimeError("Invalid PDF image manifest field")
        if parsed_id in seen_ids:
            raise RuntimeError("Invalid PDF image manifest duplicate image ID")
        seen_ids.add(parsed_id)
        validated.append(item)
    return validated


def parse_persisted_image_manifest(
    payload: bytes, *, book_id: str | None = None, generation: int | None = None
) -> list[dict[str, Any]]:
    """Validate an immutable image manifest before trusting keys or identities."""
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid image manifest JSON") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "images"}:
        raise ValueError("Invalid image manifest root")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported image manifest schema")
    images = document["images"]
    if not isinstance(images, list):
        raise ValueError("Image manifest entries must be a list")
    validated: list[dict[str, Any]] = []
    image_ids: set[str] = set()
    object_keys: set[str] = set()
    for item in images:
        if not isinstance(item, dict) or set(item) != PERSISTED_IMAGE_FIELDS:
            raise ValueError("Invalid persisted image manifest entry schema")
        image_id, image_sha, object_key, ext = (
            item["image_id"], item["image_sha256"], item["object_key"], item["ext"]
        )
        if not all(isinstance(value, str) for value in (image_id, image_sha, object_key, ext)):
            raise ValueError("Image manifest identity fields must be strings")
        try:
            uuid.UUID(image_id)
        except ValueError as exc:
            raise ValueError("Invalid persisted image UUID") from exc
        if len(image_sha) != 64 or any(value not in "0123456789abcdef" for value in image_sha):
            raise ValueError("Invalid persisted image SHA-256")
        if ext not in PERSISTED_IMAGE_EXTENSIONS:
            raise ValueError("Unsupported persisted image extension")
        if any(type(item[field]) is not int for field in ("page_number", "width", "height")):
            raise ValueError("Persisted image geometry must be integers")
        if item["page_number"] < 0 or item["width"] < 0 or item["height"] < 0:
            raise ValueError("Invalid persisted image geometry")
        if book_id is not None and generation is not None:
            expected_key = f"books/{book_id}/generations/{generation}/images/{image_id}.{ext}"
        else:
            parts = object_key.split("/")
            if len(parts) != 6 or parts[0] != "books" or parts[2] != "generations" or parts[4] != "images":
                raise ValueError("Noncanonical persisted image object key")
            expected_key = f"{'/'.join(parts[:5])}/{image_id}.{ext}"
        if object_key != expected_key or ".." in object_key.split("/"):
            raise ValueError("Noncanonical persisted image object key")
        if image_id in image_ids or object_key in object_keys:
            raise ValueError("Duplicate persisted image identity")
        image_ids.add(image_id)
        object_keys.add(object_key)
        validated.append(dict(item))
    return validated


def extract_images_from_pdf(
    pdf_path: str,
    book_id: str,
    book_title: str,
    include_full_page: bool = False,
) -> list[dict[str, Any]]:
    """Extract PDF images in a bounded subprocess and verify returned artifacts."""
    from api.services.pdf_limits import (
        DEFAULT_MAX_OUTPUT_BYTES,
        pdf_limit_environment,
    )

    with tempfile.TemporaryDirectory(prefix="grimoire-pdf-images-") as temp:
        output_dir = Path(temp) / "output"
        env = {**os.environ, **pdf_limit_environment()}
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "api.services.pdf_image_worker",
                    pdf_path,
                    str(output_dir),
                    book_id,
                    book_title,
                    "1" if include_full_page else "0",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PDF image extraction timed out") from exc
        manifest_path = output_dir / "manifest.json"
        if result.returncode == TERMINAL_DOCUMENT_EXIT_CODE:
            raise ImageDocumentError(
                result.stderr.strip() or "Invalid PDF image document"
            )
        if result.returncode != 0 or not manifest_path.exists():
            raise RuntimeError(result.stderr.strip() or "PDF image extraction failed")
        try:
            manifest_payload = read_bounded_regular_file(
                manifest_path, max_bytes=2_000_000
            )
            metadata = _validate_pdf_image_manifest(
                json.loads(manifest_payload), book_id=book_id, book_title=book_title
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Invalid PDF image manifest artifact") from exc
        extracted = []
        total_bytes = 0
        for item in metadata:
            filename = item.pop("filename")
            try:
                payload = read_bounded_regular_file(
                    output_dir / filename,
                    max_bytes=DEFAULT_MAX_OUTPUT_BYTES - total_bytes,
                )
            except OSError as exc:
                raise RuntimeError("Invalid PDF image artifact") from exc
            total_bytes += len(payload)
            if hashlib.sha256(payload).hexdigest() != item["image_sha256"]:
                raise RuntimeError("PDF image artifact hash mismatch")
            extracted.append({**item, "image_bytes": payload})
        return extracted


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
    with tempfile.TemporaryDirectory(prefix="verify-image-") as directory:
        destination = Path(directory) / "image"
        object_store.download_to(object_key, destination)
        persisted = read_bounded_regular_file(destination, max_bytes=len(payload_bytes))
    if hashlib.sha256(persisted).digest() != hashlib.sha256(payload_bytes).digest():
        raise OSError(f"Image object checksum mismatch for {object_key}")

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
