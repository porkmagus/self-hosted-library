from types import SimpleNamespace

from qdrant_client.models import Distance, VectorParams

from api.services import image_svc
from api.services.image_svc import (
    build_image_point_id,
    index_image,
    init_image_collection,
)


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
