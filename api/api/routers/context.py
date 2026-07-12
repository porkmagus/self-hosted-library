"""Context router — expandable context for search results."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from api.config import settings
from api.services.qdrant_svc import get_qdrant_client

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_CONTEXT_POINTS = 200


class ContextRequest(BaseModel):
    chunk_id: str
    book_id: str
    page_number: int = 0
    pages_before: int = 2
    pages_after: int = 2


@router.post("/context")
async def get_context_post(req: ContextRequest) -> dict[str, Any]:
    """POST with JSON body."""
    return await _get_context(req)


@router.get("/context")
async def get_context_get(
    chunk_id: str,
    book_id: str,
    page_number: int = 0,
    pages_before: int = 2,
    pages_after: int = 2,
) -> dict[str, Any]:
    """GET with query params."""
    return await _get_context(
        ContextRequest(
            chunk_id=chunk_id,
            book_id=book_id,
            page_number=page_number,
            pages_before=pages_before,
            pages_after=pages_after,
        )
    )


async def _get_context(req: ContextRequest) -> dict[str, Any]:
    client = get_qdrant_client()
    matched_chunk: Any | None = None
    try:
        pts = client.retrieve(
            collection_name=settings.QDRANT_COLLECTION,
            ids=[req.chunk_id],
            with_payload=True,
        )
        if pts:
            matched_chunk = pts[0]
    except Exception:
        logger.warning("Failed to retrieve chunk %s for book %s", req.chunk_id, req.book_id)

    page_min = max(0, req.page_number - req.pages_before)
    page_max = req.page_number + req.pages_after
    must: list[FieldCondition] = [
        FieldCondition(key="book_id", match=MatchValue(value=req.book_id)),
        FieldCondition(key="page_number", range=Range(gte=page_min, lte=page_max)),
    ]
    context_chunks: list[Any] = []
    offset: Any | None = None
    while len(context_chunks) < MAX_CONTEXT_POINTS:
        pts, nxt = client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=Filter(must=must),  # type: ignore[arg-type]
            limit=min(100, MAX_CONTEXT_POINTS - len(context_chunks)),
            offset=offset,
            with_payload=True,
        )
        context_chunks.extend(pts)
        if nxt is None:
            break
        offset = nxt

    truncated = len(context_chunks) >= MAX_CONTEXT_POINTS

    def _page_key(p: Any) -> tuple[int, int]:
        payload = p.payload or {}
        return (
            int(payload.get("page_number", 0) or 0),
            int(payload.get("chunk_index", 0) or 0),
        )

    context_chunks.sort(key=_page_key)
    total = client.count(
        collection_name=settings.QDRANT_COLLECTION,
        count_filter=Filter(
            must=[FieldCondition(key="book_id", match=MatchValue(value=req.book_id))]
        ),
    )

    pages: dict[int, list[str]] = {}
    for pt in context_chunks:
        payload = pt.payload or {}
        pg = int(payload.get("page_number", 0) or 0)
        content = str(payload.get("content", "") or "")
        pages.setdefault(pg, []).append(content)

    matched_text = ""
    if matched_chunk is not None:
        payload = matched_chunk.payload or {}
        matched_text = str(payload.get("content", "") or "")

    full_page = " ".join(pages.get(req.page_number, []))
    if matched_text and full_page:
        anchor = matched_text[:100].strip()
        if anchor and anchor in full_page:
            full_page = full_page.replace(anchor, "[>>>" + anchor + "<<<]", 1)

    return {
        "chunk_id": req.chunk_id,
        "book_id": req.book_id,
        "matched_page": req.page_number,
        "matched_text": matched_text,
        "highlighted": full_page,
        "surrounding_pages": {
            str(pg): " ".join(chunks)
            for pg, chunks in sorted(pages.items())
            if pg != req.page_number
        },
        "page_count": len(pages),
        "total_points": int(getattr(total, "count", 0) or 0),
        "available_pages": sorted(pages.keys()),
        "truncated": truncated,
    }
