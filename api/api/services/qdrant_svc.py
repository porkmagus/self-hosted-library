"""Qdrant vector database service — hybrid search with dense vectors + keyword."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchText,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from api.config import settings

_qdrant_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
    return _qdrant_client


def init_collection() -> QdrantClient:
    client = get_qdrant_client()
    existing = {col.name for col in client.get_collections().collections}

    if settings.QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=settings.EMBED_DIMENSION, distance=Distance.COSINE
            ),
        )
    info = client.get_collection(settings.QDRANT_COLLECTION)
    schema = getattr(info, "payload_schema", {}) or {}
    desired = {
        "content": PayloadSchemaType.TEXT,
        "book_id": PayloadSchemaType.KEYWORD,
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
        point_id = str(uuid5(NAMESPACE_DNS, f"{book_id}:{page_number}:{idx}"))
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "book_id": book_id,
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
    from api.services.search_cache import bump_index_generation
    bump_index_generation()
    return len(points)


def search_hybrid(
    query: str,
    query_vector: list[float] | None,
    book_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Indexed dense + lexical retrieval fused with reciprocal rank fusion."""
    client = get_qdrant_client()
    dense_records: list[Any] = []
    if query_vector is not None:
        response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            query_filter=_build_filter(book_id),
            with_payload=True,
        )
        dense_records = list(response.points)

    must = [FieldCondition(key="content", match=MatchText(text=query.strip()))]
    if book_id:
        must.append(FieldCondition(key="book_id", match=MatchValue(value=book_id)))
    keyword_records, _ = client.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must),  # type: ignore[arg-type]
        limit=min(limit, 50),
        with_payload=True,
        with_vectors=False,
    )
    return _rrf_fuse(dense_records, list(keyword_records), limit)


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
                "page_number": payload.get("page_number", 0),
                "score": round(score, 4),
            }
        )
    return result


def _build_filter(book_id: str | None = None) -> Filter | None:
    if not book_id:
        return None
    return Filter(must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))])


def get_collection_stats() -> dict[str, Any]:
    client = get_qdrant_client()
    info = client.get_collection(settings.QDRANT_COLLECTION)
    return {
        "points_count": getattr(info, "points_count", 0),
        "indexed_vectors_count": getattr(info, "indexed_vectors_count", 0),
        "vectors_count": getattr(info, "vectors_count", 0),
    }
