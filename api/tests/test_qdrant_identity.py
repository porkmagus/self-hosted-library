from types import SimpleNamespace

import pytest

from api.services import qdrant_svc
from api.services.qdrant_svc import (
    EmbeddedChunk,
    _build_filter,
    activate_generation,
    build_chunk_point_id,
    upsert_ingestion_chunks,
)

MANIFEST = "a" * 64


def test_chunk_point_id_is_deterministic_and_identity_scoped() -> None:
    first = build_chunk_point_id("book-uuid", 3, MANIFEST, 42, "root.L")
    replay = build_chunk_point_id("book-uuid", 3, MANIFEST, 42, "root.L")
    next_generation = build_chunk_point_id("book-uuid", 4, MANIFEST, 42, "root.L")
    changed_manifest = build_chunk_point_id("book-uuid", 3, "b" * 64, 42, "root.L")
    successor_claim = build_chunk_point_id("book-uuid", 3, MANIFEST, 42, "root.L", 2)

    assert first == replay
    assert first != next_generation
    assert first != changed_manifest
    assert first != successor_claim


def test_upsert_ids_do_not_depend_on_successful_result_order(monkeypatch) -> None:
    captured: list = []

    class Client:
        def upsert(self, *, collection_name, points, wait) -> None:
            captured.append((collection_name, points, wait))

    monkeypatch.setattr(qdrant_svc, "get_qdrant_client", lambda: Client())
    monkeypatch.setattr(qdrant_svc, "bump_index_generation", lambda: None)
    chunks = [
        EmbeddedChunk(8, "root.R", "fragment", [0.8, 0.1]),
        EmbeddedChunk(3, "root", "whole chunk", [0.3, 0.0]),
    ]

    count = upsert_ingestion_chunks(
        chunks,
        book_id="book-uuid",
        generation=2,
        claim_epoch=9,
        job_uuid="job-uuid",
        manifest_sha256=MANIFEST,
        embedding_signature="embed|dim=2",
        book_title="Book",
        source_key="books/book-uuid/source.pdf",
    )

    assert count == 2
    _, points, wait = captured[0]
    assert wait is True
    by_chunk = {point.payload["chunk_index"]: point for point in points}
    assert str(by_chunk[8].id) == build_chunk_point_id(
        "book-uuid", 2, MANIFEST, 8, "root.R", 9
    )
    assert str(by_chunk[3].id) == build_chunk_point_id(
        "book-uuid", 2, MANIFEST, 3, "root", 9
    )
    assert by_chunk[8].payload["generation"] == 2
    assert by_chunk[8].payload["visible"] is False
    assert by_chunk[8].payload["fragment_path"] == "root.R"
    assert by_chunk[8].payload["job_uuid"] == "job-uuid"


def test_activation_verifies_and_stamps_only_committed_claim_batches(
    monkeypatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def retrieve(self, **kwargs):
            calls.append(("retrieve", kwargs))
            return [
                SimpleNamespace(id=point_id, payload={}, vector=[0.1])
                for point_id in kwargs["ids"]
            ]

        def set_payload(self, **kwargs):
            calls.append(("set_payload", kwargs["payload"]))

    monkeypatch.setattr(qdrant_svc, "get_qdrant_client", lambda: Client())
    monkeypatch.setattr(qdrant_svc, "bump_index_generation", lambda: None)

    activate_generation(
        "book-uuid",
        2,
        job_uuid="job-uuid",
        manifest_sha256=MANIFEST,
        expected_points=[
            {"id": "point-1", "payload": {}},
            {"id": "point-2", "payload": {}},
        ],
        activation_token="winner",
    )

    assert [call[0] for call in calls] == ["retrieve", "set_payload"]
    assert calls[1][1] == {"visible": True, "activation_token": "winner"}


def test_search_prefilters_activation_tokens_before_bounded_top_n() -> None:
    query_filter = _build_filter(None, ["winner-a", "winner-b"])
    must = {condition.key: condition for condition in query_filter.must or []}
    should = {condition.key: condition for condition in query_filter.should or []}

    assert set(must) == {"visible"}
    assert set(should["activation_token"].match.any) == {"winner-a", "winner-b"}


def test_activation_rejects_wrong_payload_even_when_count_and_id_match(
    monkeypatch,
) -> None:
    class Client:
        def retrieve(self, **kwargs):
            return [
                SimpleNamespace(
                    id=kwargs["ids"][0],
                    payload={"claim_epoch": 1},
                    vector=[0.1],
                )
            ]

    monkeypatch.setattr(qdrant_svc, "get_qdrant_client", Client)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        activate_generation(
            "book-uuid",
            2,
            job_uuid="job-uuid",
            manifest_sha256=MANIFEST,
            expected_points=[{"id": "point-1", "payload": {"claim_epoch": 9}}],
            activation_token="winner",
        )
