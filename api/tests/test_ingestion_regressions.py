import ast
import hashlib
import inspect
from types import SimpleNamespace

import pytest
from qdrant_client.models import Distance, VectorParams

from api.models import BookStatus
from api.services import image_svc, ingestion_pipeline
from api.services.ingestion_pipeline import build_standalone_image_entry


def test_standalone_image_entry_has_deterministic_identity() -> None:
    payload = b"standalone-image"

    first = build_standalone_image_entry(
        payload,
        extension=".JPG",
        book_uuid="11111111-1111-1111-1111-111111111111",
        book_title="Test Image",
        width=640,
        height=480,
    )
    replay = build_standalone_image_entry(
        payload,
        extension=".JPG",
        book_uuid="11111111-1111-1111-1111-111111111111",
        book_title="Test Image",
        width=640,
        height=480,
    )

    assert first == replay
    assert first["image_sha256"] == hashlib.sha256(payload).hexdigest()
    assert first["ext"] == "jpg"
    assert first["width"] == 640
    assert first["height"] == 480


def test_image_collection_schema_rejects_unnamed_or_wrong_sized_vectors() -> None:
    unnamed_384 = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=VectorParams(size=384, distance=Distance.COSINE)
            )
        )
    )

    with pytest.raises(RuntimeError, match="library_images.*named 512-dimensional 'image'"):
        image_svc.validate_image_collection_schema("library_images", unnamed_384)


def test_image_collection_schema_accepts_named_512_vector() -> None:
    named_512 = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors={"image": VectorParams(size=512, distance=Distance.COSINE)}
            )
        )
    )

    image_svc.validate_image_collection_schema("library_images", named_512)


def test_pipeline_only_references_real_book_status_members() -> None:
    tree = ast.parse(inspect.getsource(ingestion_pipeline))
    referenced = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "BookStatus"
    }
    missing = sorted(name for name in referenced if not hasattr(BookStatus, name))
    assert missing == []
