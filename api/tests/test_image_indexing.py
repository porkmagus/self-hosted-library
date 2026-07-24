import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from qdrant_client.models import Distance, VectorParams

from api.services import image_svc
from api.services.image_svc import (
    _extract_images_in_process as extract_images_from_pdf,
)
from api.services.image_svc import (
    build_image_point_id,
    index_image,
    init_image_collection,
)
from api.services.pdf_limits import DocumentLimitError


def _image_info(width, height, xref, bbox=(0.0, 0.0, 5.0, 5.0)):
    return {"width": width, "height": height, "bbox": bbox, "xref": xref}


def _install_pdf(monkeypatch, infos, payloads):
    class Page:
        rect = SimpleNamespace(width=72.0, height=72.0, get_area=lambda: 100.0)

        def get_image_info(self, *, hashes, xrefs):
            assert hashes and xrefs
            return infos

        def get_images(self, full=True):
            raise AssertionError("resource entries must not drive extraction")

    class Document:
        def __len__(self):
            return 1

        def load_page(self, index):
            assert index == 0
            return Page()

        def extract_image(self, xref):
            return {"image": payloads[xref]}

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _path: Document()))


def test_pdf_image_extraction_skips_one_corrupt_xref(monkeypatch, caplog) -> None:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    _install_pdf(
        monkeypatch,
        [
            _image_info(64, 64, 1),
            _image_info(64, 64, 2),
            _image_info(64, 64, 3, (0.0, 0.0, 10.0, 9.5)),
        ],
        {1: output.getvalue(), 2: b"corrupt", 3: output.getvalue()},
    )

    images = extract_images_from_pdf("book.pdf", "book-id", "Book")

    assert len(images) == 1
    assert images[0]["page_number"] == 0
    assert "skipped_corrupt=1 skipped_full_page=1" in caplog.text


def test_pdf_image_extraction_propagates_geometry_failures(monkeypatch) -> None:
    class BrokenBox:
        def get_area(self):
            raise RuntimeError("page geometry unavailable")

    _install_pdf(monkeypatch, [_image_info(64, 64, 1, BrokenBox())], {1: b"image"})

    with pytest.raises(RuntimeError, match="page geometry unavailable"):
        extract_images_from_pdf("book.pdf", "book-id", "Book")


def test_pdf_image_extraction_skips_tiny_glyph_strips(monkeypatch, caplog) -> None:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    infos = [
        _image_info(3, 1, 1),
        _image_info(31, 1000, 1),
        _image_info(32, 127, 1),
        _image_info(32, 128, 1),
        _image_info(64, 64, 1),
    ]
    _install_pdf(monkeypatch, infos, {1: output.getvalue()})

    images = extract_images_from_pdf("book.pdf", "book-id", "Book")

    assert [(image["width"], image["height"]) for image in images] == [
        (32, 128),
        (64, 64),
    ]
    assert "skipped_tiny=3" in caplog.text


def test_pdf_image_count_limit_is_terminal(monkeypatch) -> None:
    monkeypatch.setenv("PDF_IMAGE_MAX_IMAGES", "1")
    _install_pdf(
        monkeypatch,
        [_image_info(64, 64, 1), _image_info(64, 64, 1)],
        {1: b"image"},
    )

    with pytest.raises(DocumentLimitError, match="count limit"):
        extract_images_from_pdf("book.pdf", "book-id", "Book")


def test_oversized_pdf_image_is_terminal_not_tiny(monkeypatch) -> None:
    monkeypatch.setenv("PDF_IMAGE_MAX_PIXELS", "4096")
    _install_pdf(monkeypatch, [_image_info(65, 65, 1)], {1: b"image"})

    with pytest.raises(DocumentLimitError, match="pixel limit"):
        extract_images_from_pdf("book.pdf", "book-id", "Book")


def test_pdf_image_extraction_ignores_unused_resource_images(monkeypatch) -> None:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    _install_pdf(
        monkeypatch,
        [_image_info(64, 64, 1), _image_info(64, 64, 1)],
        {1: output.getvalue()},
    )

    images = extract_images_from_pdf("book.pdf", "book-id", "Book")

    assert len(images) == 2


def test_public_pdf_image_extraction_runs_isolated(tmp_path, monkeypatch) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    payload = b"normalized-image"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[4])
        output.mkdir()
        (output / "image-00000.png").write_bytes(payload)
        (output / "manifest.json").write_text(
            json.dumps(
                [
                    {
                        "filename": "image-00000.png",
                        "image_id": "11111111-1111-1111-1111-111111111111",
                        "image_sha256": hashlib.sha256(payload).hexdigest(),
                        "ext": "png",
                        "page_number": 0,
                        "width": 64,
                        "height": 64,
                        "book_id": "book-id",
                        "book_title": "Book",
                    }
                ]
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    images = image_svc.extract_images_from_pdf(str(source), "book-id", "Book", True)

    command, kwargs = calls[0]
    assert command[1:3] == ["-m", "api.services.pdf_image_worker"]
    assert command[-1] == "1"
    assert 0 < kwargs["timeout"] <= 600
    assert int(kwargs["env"]["PDF_MAX_MEMORY_BYTES"]) > 0
    assert images[0]["image_bytes"] == payload


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def upload_stream(self, key, stream, *, length, content_type):
        payload = stream.read()
        assert len(payload) == length
        self.objects[key] = payload

    def stat(self, key: str):
        return SimpleNamespace(size=len(self.objects[key]))

    def download_to(self, key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[key])


class Qdrant:
    def __init__(self) -> None:
        self.calls = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)


def test_image_indexing_is_private_hidden_and_generation_scoped() -> None:
    store = Store()
    qdrant = Qdrant()
    image = {
        "image_id": "11111111-1111-1111-1111-111111111111",
        "book_id": "22222222-2222-2222-2222-222222222222",
        "book_title": "Illustrated",
        "page_number": 3,
        "image_bytes": b"valid-image-bytes",
        "image_sha256": "a" * 64,
        "ext": "png",
        "width": 64,
        "height": 48,
    }

    point_id = index_image(
        image,
        [0.1, 0.2],
        generation=4,
        claim_epoch=7,
        image_index=0,
        job_uuid="33333333-3333-3333-3333-333333333333",
        manifest_sha256="b" * 64,
        embedding_signature="clip|dim=2",
        store=store,
        qdrant=qdrant,
    )

    expected_key = (
        "books/22222222-2222-2222-2222-222222222222/"
        "generations/4/images/11111111-1111-1111-1111-111111111111.png"
    )
    assert store.objects[expected_key] == b"valid-image-bytes"
    call = qdrant.calls[0]
    assert call["wait"] is True
    payload = call["points"][0].payload
    assert payload["visible"] is False
    assert payload["generation"] == 4
    assert payload["claim_epoch"] == 7
    assert payload["job_uuid"] == "33333333-3333-3333-3333-333333333333"
    assert payload["manifest_sha256"] == "b" * 64
    assert payload["object_key"] == expected_key
    assert "image_url" not in payload
    assert str(call["points"][0].id) == point_id


def test_image_point_identity_is_claim_scoped_against_late_stale_writes() -> None:
    first = build_image_point_id("book", 4, "manifest", "image", claim_epoch=7)
    successor = build_image_point_id("book", 4, "manifest", "image", claim_epoch=8)

    assert first != successor


def test_existing_image_collection_gets_generation_visibility_indexes(
    monkeypatch,
) -> None:
    class ExistingCollection:
        def __init__(self) -> None:
            self.indexes: list[tuple[str, object]] = []
            self.payloads = []

        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name="library_images")])

        def get_collection(self, _name):
            return SimpleNamespace(
                payload_schema={},
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors={
                            "image": VectorParams(size=512, distance=Distance.COSINE)
                        }
                    )
                ),
            )

        def create_payload_index(
            self, collection_name, field_name, field_schema, wait=True
        ):
            assert collection_name == "library_images"
            assert wait is True
            self.indexes.append((field_name, field_schema))

        def set_payload(self, **kwargs):
            self.payloads.append(kwargs)

    client = ExistingCollection()
    monkeypatch.setattr(image_svc, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(image_svc.settings, "IMAGE_COLLECTION", "library_images")

    init_image_collection()

    assert {name for name, _ in client.indexes} >= {
        "book_id",
        "generation",
        "visible",
        "job_uuid",
        "manifest_sha256",
    }
    assert client.payloads[0]["payload"] == {"visible": True}


def test_image_generation_activation_verifies_count_then_switches_visibility() -> None:
    class Client:
        def __init__(self) -> None:
            self.payloads = []

        def retrieve(self, **kwargs):
            return [
                SimpleNamespace(id=point_id, payload={}, vector={"image": [0.1]})
                for point_id in kwargs["ids"]
            ]

        def set_payload(self, **kwargs):
            self.payloads.append(kwargs)

    client = Client()
    image_svc.activate_image_generation(
        "22222222-2222-2222-2222-222222222222",
        4,
        job_uuid="33333333-3333-3333-3333-333333333333",
        manifest_sha256="b" * 64,
        expected_points=[
            {"id": "point-1", "payload": {}},
            {"id": "point-2", "payload": {}},
        ],
        activation_token="winner",
        qdrant=client,
    )

    assert [call["payload"] for call in client.payloads] == [
        {"visible": True, "activation_token": "winner"}
    ]
    assert all(call["wait"] is True for call in client.payloads)


def test_image_search_only_returns_visible_private_api_urls(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.query = None

        def query_points(self, **kwargs):
            self.query = kwargs
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        score=0.9,
                        payload={
                            "image_id": "11111111-1111-1111-1111-111111111111",
                            "book_id": "22222222-2222-2222-2222-222222222222",
                            "book_title": "Illustrated",
                            "generation": 4,
                            "ext": "png",
                            "page_number": 3,
                            "visible": True,
                        },
                    )
                ]
            )

    client = Client()
    monkeypatch.setattr(image_svc, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(
        image_svc, "get_text_embedding_for_search", lambda _query: [0.1, 0.2]
    )

    results = image_svc.search_images("sigil")

    assert results[0]["image_url"] == (
        "/api/books/22222222-2222-2222-2222-222222222222/"
        "images/4/11111111-1111-1111-1111-111111111111.png"
    )
    query_filter = client.query["query_filter"]
    assert query_filter.must[0].key == "visible"
    assert query_filter.must[0].match.value is True


def test_image_search_drops_low_relevance_matches(monkeypatch) -> None:
    class Client:
        def query_points(self, **_kwargs):
            return SimpleNamespace(
                points=[SimpleNamespace(score=0.05, payload={"visible": True})]
            )

    monkeypatch.setattr(image_svc, "get_qdrant_client", Client)
    monkeypatch.setattr(
        image_svc, "get_text_embedding_for_search", lambda _query: [0.1, 0.2]
    )
    monkeypatch.setattr(image_svc.settings, "IMAGE_SEARCH_MIN_SCORE", 0.2)

    assert image_svc.search_images("completely unrelated") == []
