"""Qdrant vector database service — hybrid search with dense vectors + keyword."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    IsEmptyCondition,
    MatchAny,
    MatchText,
    MatchValue,
    PayloadField,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    Range,
    VectorParams,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.models import Book, BookStatus
from api.services.search_cache import bump_index_generation

_qdrant_client: QdrantClient | None = None
APP_VECTOR_NAMESPACE = UUID("4f0a5c0e-fd91-4b58-8cc9-0fd1785cccad")


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk_index: int
    fragment_path: str
    text: str
    vector: list[float]


def build_chunk_point_id(
    book_id: str,
    generation: int,
    manifest_sha256: str,
    chunk_index: int,
    fragment_path: str = "root",
    claim_epoch: int = 0,
) -> str:
    """Return a stable point ID independent of batching and retry outcomes."""
    identity = (
        f"grimoire:{book_id}:{generation}:{manifest_sha256}:"
        f"claim:{claim_epoch}:{chunk_index}:{fragment_path}"
    )
    return str(uuid5(APP_VECTOR_NAMESPACE, identity))


def upsert_ingestion_chunks(
    chunks: list[EmbeddedChunk],
    *,
    book_id: str,
    generation: int,
    claim_epoch: int,
    job_uuid: str,
    manifest_sha256: str,
    embedding_signature: str,
    book_title: str,
    source_key: str,
) -> int:
    """Upsert canonical chunks using generation-scoped deterministic IDs."""
    points = [
        PointStruct(
            id=build_chunk_point_id(
                book_id,
                generation,
                manifest_sha256,
                chunk.chunk_index,
                chunk.fragment_path,
                claim_epoch,
            ),
            vector=chunk.vector,
            payload={
                "book_id": book_id,
                "job_uuid": job_uuid,
                "generation": generation,
                "claim_epoch": claim_epoch,
                "manifest_sha256": manifest_sha256,
                "embedding_signature": embedding_signature,
                "visible": False,
                "title": book_title,
                "source": source_key,
                "chunk_index": chunk.chunk_index,
                "fragment_path": chunk.fragment_path,
                "content": chunk.text,
            },
        )
        for chunk in chunks
    ]
    if not points:
        return 0
    get_qdrant_client().upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=points,
        wait=True,
    )
    bump_index_generation()
    return len(points)


def delete_book_generation(book_id: str, generation: int) -> None:
    """Remove a stale worker's generation after ownership loss."""
    get_qdrant_client().delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="book_id", match=MatchValue(value=book_id)),
                FieldCondition(key="generation", match=MatchValue(value=generation)),
            ]
        ),
        wait=True,
    )
    bump_index_generation()


def delete_prior_generations(book_id: str, generation: int) -> None:
    """Remove superseded vectors after the replacement generation completes."""
    get_qdrant_client().delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="book_id", match=MatchValue(value=book_id)),
                FieldCondition(key="generation", range=Range(lt=generation)),
            ]
        ),
        wait=True,
    )
    bump_index_generation()


def activate_generation(
    book_id: str,
    generation: int,
    *,
    job_uuid: str,
    manifest_sha256: str,
    expected_points: list[dict[str, Any]],
    activation_token: str,
) -> None:
    """Verify exact accepted IDs and stamp only that immutable set."""
    if not expected_points:
        return
    client = get_qdrant_client()
    ids: list[int | str | UUID] = [str(point["id"]) for point in expected_points]
    records = client.retrieve(
        collection_name=settings.QDRANT_COLLECTION,
        ids=ids,
        with_payload=True,
        with_vectors=True,
    )
    actual = {str(record.id): record for record in records}
    if set(actual) != set(ids):
        raise RuntimeError("Accepted text vector IDs are missing or unexpected")
    for expected in expected_points:
        record = actual[str(expected["id"])]
        payload = record.payload or {}
        if any(payload.get(key) != value for key, value in expected["payload"].items()):
            raise RuntimeError(f"Text vector identity mismatch for {expected['id']}")
        if record.vector is None:
            raise RuntimeError(f"Text vector is missing for {expected['id']}")
    for offset in range(0, len(ids), 512):
        client.set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"visible": True, "activation_token": activation_token},
            points=PointIdsList(points=ids[offset : offset + 512]),
            wait=True,
        )
    bump_index_generation()


def retire_other_activations(book_id: str, activation_token: str) -> None:
    """Hide only identities PostgreSQL has proven are no longer current."""
    client = get_qdrant_client()
    client.set_payload(
        collection_name=settings.QDRANT_COLLECTION,
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
    bump_index_generation()


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
    return _qdrant_client


def init_collection() -> QdrantClient:
    client = get_qdrant_client()
    existing = {col.name for col in client.get_collections().collections}
    is_new = settings.QDRANT_COLLECTION not in existing

    if is_new:
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=settings.EMBED_DIMENSION, distance=Distance.COSINE
            ),
            hnsw_config=HnswConfigDiff(
                m=16,
                ef_construct=100,
                max_indexing_threads=2,
                on_disk=True,
            ),
        )

    if is_new:
        desired = {
            "content": PayloadSchemaType.TEXT,
            "book_id": PayloadSchemaType.KEYWORD,
            "job_uuid": PayloadSchemaType.KEYWORD,
            "manifest_sha256": PayloadSchemaType.KEYWORD,
            "generation": PayloadSchemaType.INTEGER,
            "claim_epoch": PayloadSchemaType.INTEGER,
            "activation_token": PayloadSchemaType.KEYWORD,
            "visible": PayloadSchemaType.BOOL,
            "title": PayloadSchemaType.TEXT,
        }
        for field, field_schema in desired.items():
            client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name=field,
                field_schema=field_schema,
                wait=True,
            )
    else:
        info = client.get_collection(settings.QDRANT_COLLECTION)
        schema = getattr(info, "payload_schema", {}) or {}
        desired = {
            "content": PayloadSchemaType.TEXT,
            "book_id": PayloadSchemaType.KEYWORD,
            "job_uuid": PayloadSchemaType.KEYWORD,
            "manifest_sha256": PayloadSchemaType.KEYWORD,
            "generation": PayloadSchemaType.INTEGER,
            "claim_epoch": PayloadSchemaType.INTEGER,
            "activation_token": PayloadSchemaType.KEYWORD,
            "visible": PayloadSchemaType.BOOL,
            "title": PayloadSchemaType.TEXT,
        }
        for field, field_schema in desired.items():
            if field not in schema:
                client.create_payload_index(
                    collection_name=settings.QDRANT_COLLECTION,
                    field_name=field,
                    field_schema=field_schema,
                    wait=True,
                )
        client.set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"visible": True},
            points=Filter(
                must=[IsEmptyCondition(is_empty=PayloadField(key="visible"))]
            ),
            wait=True,
        )
    return client


def upsert_chunks(
    chunks: list[tuple[str, list[float]]],
    book_id: str,
    book_title: str,
    source_key: str,
    page_number: int,
) -> int:
    client = get_qdrant_client()
    points: list[PointStruct] = []
    for idx, (text, vector) in enumerate(chunks):
        point_id = str(
            uuid5(APP_VECTOR_NAMESPACE, f"legacy:{book_id}:{page_number}:{idx}")
        )
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "book_id": book_id,
                    "visible": True,
                    "title": book_title,
                    "source": source_key,
                    "page_number": page_number,
                    "chunk_index": idx,
                    "content": text,
                },
            )
        )
    if not points:
        return 0
    client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
    bump_index_generation()
    return len(points)


def search_hybrid(
    query: str,
    query_vector: list[float] | None,
    book_id: str | None = None,
    limit: int = 20,
    activation_tokens: list[str] | None = None,
    legacy_identities: list[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Indexed dense + lexical retrieval fused with reciprocal rank fusion."""
    if activation_tokens == [] and not legacy_identities:
        return []
    client = get_qdrant_client()
    dense_records: list[Any] = []
    if query_vector is not None:
        response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            query_filter=_build_filter(book_id, activation_tokens, legacy_identities),
            with_payload=True,
        )
        dense_records = list(response.points)

    scoped_filter = _build_filter(book_id, activation_tokens, legacy_identities)
    must = [
        FieldCondition(key="content", match=MatchText(text=query.strip())),
        *(scoped_filter.must or []),
    ]
    keyword_records, _ = client.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must, should=scoped_filter.should),
        limit=min(limit, 50),
        with_payload=True,
        with_vectors=False,
    )
    return _rrf_fuse(
        dense_records, _rank_lexical_records(list(keyword_records), query), limit
    )


def _rank_lexical_records(records: list[Any], query: str) -> list[Any]:
    """Give Qdrant text-filter candidates a deterministic relevance order."""
    terms = tuple(dict.fromkeys(re.findall(r"[\w]+", query.casefold())))
    if not terms:
        return records

    def score(record: Any) -> float:
        text = str((record.payload or {}).get("content", "")).casefold()
        counts = [text.count(term) for term in terms]
        coverage = sum(count > 0 for count in counts) / len(terms)
        frequency = sum(math.log1p(count) for count in counts)
        phrase_bonus = 1.0 if query.casefold() in text else 0.0
        return coverage * 2.0 + frequency + phrase_bonus

    return sorted(records, key=score, reverse=True)


def _rrf_fuse(
    dense_records: list[Any],
    kw_records: list[Any],
    limit: int,
    k: int = 60,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    all_hits: dict[str, Any] = {}
    for rank, hit in enumerate(dense_records):
        pid = str(hit.id)
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + 1 + rank)
        all_hits[pid] = hit
    for rank, hit in enumerate(kw_records):
        pid = str(hit.id)
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + 1 + rank)
        if pid not in all_hits:
            all_hits[pid] = hit
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    result: list[dict[str, Any]] = []
    for pid, score in ranked[:limit]:
        hit = all_hits[pid]
        payload = hit.payload if getattr(hit, "payload", None) else {}
        if not isinstance(payload, dict):
            payload = {}
        result.append(
            {
                "chunk_id": pid,
                "text": payload.get("content", ""),
                "book_id": payload.get("book_id", ""),
                "book_title": payload.get("title", ""),
                "generation": payload.get("generation", 0),
                "job_uuid": payload.get("job_uuid"),
                "activation_token": payload.get("activation_token"),
                "page_number": payload.get("page_number", 0),
                "score": round(score, 4),
            }
        )
    return result


def _build_filter(
    book_id: str | None = None,
    activation_tokens: list[str] | None = None,
    legacy_identities: list[tuple[str, int]] | None = None,
) -> Filter:
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
                        key="generation", match=MatchValue(value=legacy_generation)
                    ),
                    IsEmptyCondition(is_empty=PayloadField(key="activation_token")),
                ]
            )
        )
    if book_id:
        must.append(FieldCondition(key="book_id", match=MatchValue(value=book_id)))
    return Filter(must=must, should=should or None)


def filter_current_text_generations(
    session: Session, results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fail closed unless PostgreSQL says the result generation is active."""
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


def get_collection_stats() -> dict[str, Any]:
    client = get_qdrant_client()
    info = client.get_collection(settings.QDRANT_COLLECTION)
    return {
        "points_count": getattr(info, "points_count", 0),
        "indexed_vectors_count": getattr(info, "indexed_vectors_count", 0),
        "vectors_count": getattr(info, "vectors_count", 0),
    }
