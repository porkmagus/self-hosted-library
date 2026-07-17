from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.models import Base, Book, BookStatus
from api.routers.search import authorize_cached_text_payload
from api.services import image_svc
from api.services.qdrant_svc import (
    _rank_lexical_records,
    _rrf_fuse,
    filter_current_text_generations,
)


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


def test_image_results_follow_postgres_indexed_generation() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Book(
                uuid="11111111-1111-1111-1111-111111111111",
                title="Current",
                original_filename="current.pdf",
                sanitized_filename="current.pdf",
                file_extension=".pdf",
                status=BookStatus.INDEXED,
                indexed_generation=4,
            )
        )
        session.commit()
        filtered = image_svc.filter_current_image_generations(
            session,
            [
                {
                    "book_id": "11111111-1111-1111-1111-111111111111",
                    "generation": 3,
                },
                {
                    "book_id": "11111111-1111-1111-1111-111111111111",
                    "generation": 4,
                },
            ],
        )

    assert filtered == [
        {
            "book_id": "11111111-1111-1111-1111-111111111111",
            "generation": 4,
        }
    ]


def test_text_results_fail_closed_for_deleted_or_stale_generations() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Book(
                    uuid="22222222-2222-2222-2222-222222222222",
                    title="Current",
                    original_filename="current.txt",
                    sanitized_filename="current.txt",
                    file_extension=".txt",
                    status=BookStatus.INDEXED,
                    indexed_generation=4,
                ),
                Book(
                    uuid="33333333-3333-3333-3333-333333333333",
                    title="Deleted",
                    original_filename="deleted.txt",
                    sanitized_filename="deleted.txt",
                    file_extension=".txt",
                    status=BookStatus.DELETED,
                    indexed_generation=1,
                ),
            ]
        )
        session.commit()
        filtered = filter_current_text_generations(
            session,
            [
                {
                    "book_id": "22222222-2222-2222-2222-222222222222",
                    "generation": 3,
                },
                {
                    "book_id": "22222222-2222-2222-2222-222222222222",
                    "generation": 4,
                },
                {
                    "book_id": "33333333-3333-3333-3333-333333333333",
                    "generation": 1,
                },
            ],
        )

    assert filtered == [
        {
            "book_id": "22222222-2222-2222-2222-222222222222",
            "generation": 4,
        }
    ]


def test_cached_text_payload_is_revalidated_before_return() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Book(
                uuid="44444444-4444-4444-4444-444444444444",
                title="Deleted",
                original_filename="deleted.txt",
                sanitized_filename="deleted.txt",
                file_extension=".txt",
                status=BookStatus.DELETED,
                indexed_generation=1,
            )
        )
        session.commit()
        payload = authorize_cached_text_payload(
            session,
            {
                "results": [
                    {
                        "book_id": "44444444-4444-4444-4444-444444444444",
                        "book_title": "Deleted",
                        "chunk_id": "secret",
                        "text": "private excerpt",
                        "page_number": 1,
                        "score": 1.0,
                        "generation": 1,
                    }
                ],
                "groups": [{"book_id": "44444444-4444-4444-4444-444444444444"}],
                "total": 1,
            },
        )

    assert payload["results"] == []
    assert payload["groups"] == []
    assert payload["total"] == 0


def test_lexical_candidates_are_ranked_by_query_relevance_not_storage_order() -> None:
    weak = _hit("weak", "book", "magic appears once")
    strong = _hit("strong", "book", "magic ritual magic ritual magic")

    ranked = _rank_lexical_records([weak, strong], "magic ritual")

    assert [point.id for point in ranked] == ["strong", "weak"]


def test_search_results_require_postgres_activation_token_when_present() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Book(
                uuid="55555555-5555-5555-5555-555555555555",
                title="Winner",
                original_filename="winner.pdf",
                sanitized_filename="winner.pdf",
                file_extension=".pdf",
                status=BookStatus.INDEXED,
                indexed_generation=8,
                indexed_job_uuid="66666666-6666-6666-6666-666666666666",
                indexed_activation_token="winner",
            )
        )
        session.commit()
        candidates = [
            {
                "book_id": "55555555-5555-5555-5555-555555555555",
                "generation": 8,
                "activation_token": "stale",
                "job_uuid": "66666666-6666-6666-6666-666666666666",
            },
            {
                "book_id": "55555555-5555-5555-5555-555555555555",
                "generation": 8,
                "activation_token": "winner",
                "job_uuid": "77777777-7777-7777-7777-777777777777",
            },
            {
                "book_id": "55555555-5555-5555-5555-555555555555",
                "generation": 8,
                "activation_token": "winner",
                "job_uuid": "66666666-6666-6666-6666-666666666666",
            },
        ]

        assert filter_current_text_generations(session, candidates) == [candidates[2]]
        assert image_svc.filter_current_image_generations(session, candidates) == [
            candidates[2]
        ]
