"""HTTP client for the dedicated embedding server.

Workers send batched text requests and get embeddings back.
Falls back to CPU sentence-transformers if server is down.
"""

from __future__ import annotations

import logging
from typing import cast

import httpx

from api.config import settings

logger = logging.getLogger(__name__)

_embed_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _embed_client
    if _embed_client is None:
        url = settings.EMBEDDING_SERVER_URL or "http://embedding-server:8001"
        _embed_client = httpx.Client(
            base_url=url,
            timeout=httpx.Timeout(300.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=32),
        )
    return _embed_client


def get_embedding_batch(texts: list[str]) -> list[list[float] | None]:
    """Get embeddings via the dedicated embedding server."""
    if not texts:
        return []

    max_chars = 2048
    cleaned: list[str] = []
    indices: list[int] = []
    for i, text in enumerate(texts):
        c = "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")
        c = c.strip()
        if not c:
            continue
        if len(c) > max_chars:
            c = c[:max_chars].rsplit(" ", 1)[0]
        cleaned.append(c)
        indices.append(i)

    results: list[list[float] | None] = [None] * len(texts)
    if not cleaned:
        return results

    client = _get_client()
    try:
        resp = client.post(
            "/embed",
            json={"texts": cleaned, "batch_size": 256},
        )
        resp.raise_for_status()
        data = cast(dict, resp.json())
        embeddings = cast(list, data.get("embeddings", []))
        for j, emb in enumerate(embeddings):
            if j < len(indices):
                results[indices[j]] = cast(list[float], emb)
    except Exception as e:
        logger.error("Embedding server failed: %s", e)
        # Fall back to direct sentence-transformers
        try:
            from api.services.sentence_transformer_svc import (
                get_embedding_batch as _fallback,
            )
            # Map cleaned texts back to original positions
            full_cleaned = [None] * len(texts)
            for idx, c in zip(indices, cleaned):
                full_cleaned[idx] = c
            fallback_results = _fallback(full_cleaned)
            for i in range(len(results)):
                results[i] = fallback_results[i]
        except Exception as fb_err:
            logger.error("Fallback embedding also failed: %s", fb_err)

    return results


def get_clip_image_embedding(image_bytes: bytes) -> list[float]:
    """Get a normalized CLIP image vector from the shared GPU service."""
    response = _get_client().post(
        "/embed-image",
        content=image_bytes,
        headers={"content-type": "application/octet-stream"},
    )
    response.raise_for_status()
    payload = cast(dict, response.json())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or len(embedding) != 512:
        raise ValueError("Embedding server returned an invalid CLIP image vector")
    return cast(list[float], embedding)
