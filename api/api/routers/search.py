"""Fast, bounded search with caching, coalescing, and lazy image retrieval."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.config import settings
from api.models import Book, BookStatus, get_db_session
from api.services.cross_encoder import cross_encoder_available, rerank_results
from api.services.qdrant_svc import filter_current_text_generations, search_hybrid
from api.services.search_cache import (
    acquire_flight,
    allow_request,
    get_cached,
    index_generation,
    make_search_cache_key,
    release_flight,
    set_cached,
)
from api.services.search_history import get_history, record_query
from api.services.search_utils import (
    diversify_results,
    normalize_display_text,
    normalize_query,
    retrieval_limit,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_embed_slots = asyncio.Semaphore(4)
_rerank_slots = asyncio.Semaphore(2)
_image_slots = asyncio.Semaphore(2)
_embed_client: httpx.AsyncClient | None = None
_flight_events: dict[str, asyncio.Event] = {}


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None:
        _embed_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
        )
    return _embed_client


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)
    book_id: str | None = None
    rerank: bool = True
    mode: str = "comprehensive"


class SearchResult(BaseModel):
    text: str
    book_id: str
    book_title: str
    chunk_id: str
    page_number: int
    score: float
    generation: int
    job_uuid: str | None = None
    activation_token: str | None = None
    content_type: str = "text"


def _group_by_book(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    book_map: dict[str, dict[str, Any]] = {}
    for result in results:
        book_id = str(result["book_id"])
        group = book_map.setdefault(
            book_id,
            {
                "book_id": book_id,
                "book_title": result["book_title"],
                "match_count": 0,
                "top_score": 0.0,
                "pages": [],
                "matches": [],
            },
        )
        group["match_count"] = int(group["match_count"]) + 1
        group["top_score"] = max(float(group["top_score"]), float(result["score"]))
        pages = group["pages"]
        matches = group["matches"]
        assert isinstance(pages, list)
        assert isinstance(matches, list)
        if result["page_number"] not in pages:
            pages.append(result["page_number"])
        if len(matches) < 3:
            text = str(result["text"])
            matches.append(
                {
                    "chunk_id": result["chunk_id"],
                    "page_number": result["page_number"],
                    "text": text[:500] + ("…" if len(text) > 500 else ""),
                    "score": result["score"],
                }
            )
    groups = sorted(book_map.values(), key=lambda group: -float(group["top_score"]))
    for group in groups:
        pages = group["pages"]
        assert isinstance(pages, list)
        pages.sort()
    return groups


def authorize_cached_text_payload(
    session: Any, payload: dict[str, Any]
) -> dict[str, Any]:
    """Revalidate cached excerpts against PostgreSQL before returning them."""
    authorized = filter_current_text_generations(
        session, [dict(result) for result in payload.get("results", [])]
    )
    result = dict(payload)
    result["results"] = authorized
    result["groups"] = _group_by_book(authorized)
    result["total"] = len(authorized)
    return result


def _authorize_candidates_from_db(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    with get_db_session() as session:
        return filter_current_text_generations(session, candidates)


def _authorize_payload_from_db(payload: dict[str, Any]) -> dict[str, Any]:
    with get_db_session() as session:
        return authorize_cached_text_payload(session, payload)


def _active_scope_from_db(
    book_id: str | None = None,
) -> tuple[list[str], list[tuple[str, int]]]:
    """Return tokenized identities plus exact pre-token compatibility identities."""
    with get_db_session() as session:
        statement = select(
            Book.uuid, Book.indexed_generation, Book.indexed_activation_token
        ).where(
            Book.status == BookStatus.INDEXED,
            Book.deleted_at.is_(None),
            Book.indexed_generation.is_not(None),
        )
        if book_id:
            statement = statement.where(Book.uuid == book_id)
        rows = list(session.execute(statement))
    tokens = [str(token) for _, _, token in rows if token is not None]
    legacy = [
        (str(uuid), int(generation))
        for uuid, generation, token in rows
        if token is None and generation is not None
    ]
    return tokens, legacy


def _timing_header(timings: dict[str, float]) -> str:
    return ", ".join(f"{name};dur={duration:.1f}" for name, duration in timings.items())


async def _compute(req: SearchRequest, timings: dict[str, float]) -> dict[str, Any]:
    query = normalize_query(req.query)
    started = time.perf_counter()
    query_vector: list[float] | None = None
    embed_started = time.perf_counter()
    try:
        async with _embed_slots:
            client = _get_embed_client()
            embed_url = (
                settings.EMBEDDING_SERVER_URL
                or f"{settings.OLLAMA_URL}/api/embed"
            )
            response = await client.post(
                f"{embed_url}/embed",
                json={"texts": [query]},
            )
            response.raise_for_status()
            result = response.json()
            embeddings = result.get("embeddings", [])
            if embeddings and embeddings[0] is not None:
                query_vector = list(embeddings[0])
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning(
            "Query embedding failed; using indexed lexical retrieval: %s", exc
        )
    timings["embed"] = (time.perf_counter() - embed_started) * 1000

    retrieve_started = time.perf_counter()
    candidate_limit = retrieval_limit(req.limit)
    activation_tokens, legacy_identities = await anyio.to_thread.run_sync(
        lambda: _active_scope_from_db(req.book_id)
    )
    candidates = await anyio.to_thread.run_sync(
        lambda: search_hybrid(
            query=query,
            query_vector=query_vector,
            limit=candidate_limit,
            book_id=req.book_id,
            activation_tokens=activation_tokens,
            legacy_identities=legacy_identities,
        )
    )
    candidates = await anyio.to_thread.run_sync(
        lambda: _authorize_candidates_from_db(candidates)
    )
    timings["retrieve"] = (time.perf_counter() - retrieve_started) * 1000

    reranked = False
    if req.rerank and candidates:
        rerank_started = time.perf_counter()
        async with _rerank_slots:
            available = await anyio.to_thread.run_sync(cross_encoder_available)
            if available:
                candidates = await anyio.to_thread.run_sync(
                    lambda: rerank_results(
                        query=query,
                        results=candidates,
                        top_k=candidate_limit,
                        max_candidates=candidate_limit,
                    )
                )
                reranked = True
        timings["rerank"] = (time.perf_counter() - rerank_started) * 1000

    results = diversify_results(candidates)[: req.limit]
    normalized: list[dict[str, Any]] = []
    for result in results:
        item = dict(result)
        item["text"] = normalize_display_text(str(item.get("text", "")))
        normalized.append(item)
    payload = {
        "query": query,
        "results": [
            SearchResult(
                text=str(result["text"]),
                book_id=str(result["book_id"]),
                book_title=str(result["book_title"]),
                chunk_id=str(result["chunk_id"]),
                page_number=int(result["page_number"]),
                score=float(result["score"]),
                generation=int(result["generation"]),
                job_uuid=result.get("job_uuid"),
                activation_token=result.get("activation_token"),
                content_type=str(result.get("content_type", "text")),
            ).model_dump()
            for result in normalized
        ],
        "images": [],
        "groups": _group_by_book(normalized),
        "total": len(normalized),
        "image_count": 0,
        "reranked": reranked,
        "mode": req.mode,
    }
    timings["total"] = (time.perf_counter() - started) * 1000
    return payload


async def _search(
    req: SearchRequest, request: Request, response: Response
) -> dict[str, Any]:
    query = normalize_query(req.query)
    if not query:
        return {
            "query": "",
            "results": [],
            "images": [],
            "groups": [],
            "total": 0,
            "image_count": 0,
            "reranked": False,
            "mode": req.mode,
        }
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client_id = (
        forwarded
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else "unknown")
    )
    if not await anyio.to_thread.run_sync(lambda: allow_request(client_id)):
        response.headers["Retry-After"] = "60"
        raise HTTPException(status_code=429, detail="Search rate limit exceeded")
    generation = await anyio.to_thread.run_sync(index_generation)
    key = make_search_cache_key(query, req.limit, req.book_id, req.rerank, generation)
    cached = await anyio.to_thread.run_sync(lambda: get_cached(key))
    if cached is not None:
        cached_payload = cached
        authorized_cached = await anyio.to_thread.run_sync(
            lambda: _authorize_payload_from_db(cached_payload)
        )
        if len(authorized_cached.get("results", [])) == len(
            cached_payload.get("results", [])
        ):
            response.headers["X-Search-Cache"] = "HIT"
            response.headers["Server-Timing"] = "cache;dur=0"
            return authorized_cached

    event = _flight_events.setdefault(key, asyncio.Event())
    token = uuid4().hex
    owns_flight = await anyio.to_thread.run_sync(lambda: acquire_flight(key, token))
    if not owns_flight:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=30.0)
        cached = await anyio.to_thread.run_sync(lambda: get_cached(key))
        if cached is not None:
            cached_payload = cached
            authorized_cached = await anyio.to_thread.run_sync(
                lambda: _authorize_payload_from_db(cached_payload)
            )
            if len(authorized_cached.get("results", [])) == len(
                cached_payload.get("results", [])
            ):
                response.headers["X-Search-Cache"] = "COALESCED"
                response.headers["Server-Timing"] = "cache;dur=0"
                _flight_events.pop(key, None)
                return authorized_cached
        owns_flight = await anyio.to_thread.run_sync(lambda: acquire_flight(key, token))

    timings: dict[str, float] = {}
    try:
        payload = await _compute(req, timings)
        await anyio.to_thread.run_sync(lambda: set_cached(key, payload))
    finally:
        if owns_flight:
            await anyio.to_thread.run_sync(lambda: release_flight(key, token))
        ev = _flight_events.pop(key, None)
        if ev is not None:
            ev.set()
    response.headers["X-Search-Cache"] = "MISS"
    response.headers["Server-Timing"] = _timing_header(timings)
    logger.info(
        "search query=%r results=%d timings=%s", query, payload["total"], timings
    )
    await anyio.to_thread.run_sync(lambda: record_query(query, client_id))
    return payload


@router.post("/search")
async def search(
    req: SearchRequest, request: Request, response: Response
) -> dict[str, Any]:
    return await _search(req, request, response)


@router.get("/search")
async def search_get(
    request: Request,
    response: Response,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=50),
    book_id: str | None = Query(None),
    rerank: bool = Query(True),
    mode: str = Query("comprehensive"),
) -> dict[str, Any]:
    return await _search(
        SearchRequest(query=q, limit=limit, book_id=book_id, rerank=rerank, mode=mode),
        request,
        response,
    )


@router.get("/search/images")
async def search_images_get(
    response: Response,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(12, ge=1, le=24),
) -> dict[str, Any]:
    from api.services.image_svc import (
        filter_current_image_generations,
        search_images,
    )

    started = time.perf_counter()
    activation_tokens, legacy_identities = await anyio.to_thread.run_sync(
        _active_scope_from_db
    )
    async with _image_slots:
        images = await anyio.to_thread.run_sync(
            lambda: search_images(
                normalize_query(q),
                limit=limit * 2,
                activation_tokens=activation_tokens,
                legacy_identities=legacy_identities,
            )
        )

    def filter_activated() -> list[dict[str, Any]]:
        with get_db_session() as session:
            return filter_current_image_generations(session, images)

    images = await anyio.to_thread.run_sync(filter_activated)
    images = sorted(
        (
            image
            for image in images
            if float(image.get("score", 0) or 0) >= settings.IMAGE_SEARCH_MIN_SCORE
        ),
        key=lambda image: -float(image.get("score", 0) or 0),
    )[:limit]
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"images;dur={elapsed:.1f}"
    return {"query": normalize_query(q), "images": images, "image_count": len(images)}


@router.get("/search/history")
async def search_history(
    limit: int = Query(10, ge=1, le=20),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Get recent search queries."""
    client_id = "default"
    if request:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        client_id = (
            forwarded
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else "default")
        )
    history = await anyio.to_thread.run_sync(lambda: get_history(client_id, limit))
    return {"history": history, "total": len(history)}
