from types import SimpleNamespace

from api.services.qdrant_svc import _rrf_fuse


def _hit(point_id: str, book_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=point_id,
        payload={
            "book_id": book_id,
            "title": f"Book {book_id}",
            "content": text,
            "page_number": 1,
        },
    )


def test_rrf_rewards_results_found_by_dense_and_lexical_search() -> None:
    shared = _hit("shared", "a", "shared result")
    dense_only = _hit("dense", "b", "dense result")
    lexical_only = _hit("lexical", "c", "lexical result")

    results = _rrf_fuse(
        dense_records=[dense_only, shared],
        kw_records=[shared, lexical_only],
        limit=3,
    )

    assert [result["chunk_id"] for result in results] == [
        "shared",
        "dense",
        "lexical",
    ]
    assert results[0]["text"] == "shared result"
    assert results[0]["book_id"] == "a"


def test_rrf_honors_result_limit() -> None:
    results = _rrf_fuse(
        dense_records=[_hit(str(index), "a", str(index)) for index in range(5)],
        kw_records=[],
        limit=2,
    )

    assert len(results) == 2
