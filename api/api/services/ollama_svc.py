"""Ollama embedding service — TRUE batch embedding, no artificial delays."""

from __future__ import annotations

from typing import Any, cast

import httpx

from api.config import settings

_WS = "\n\r\t"


def pull_model_if_needed() -> None:
    """Ensure the embedding model is pulled in Ollama."""
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.get(f"{settings.OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m["name"] for m in models]
                if settings.EMBED_MODEL not in model_names:
                    print(f"Pulling Ollama model: {settings.EMBED_MODEL}")
                    pull_resp = client.post(
                        f"{settings.OLLAMA_URL}/api/pull",
                        json={"name": settings.EMBED_MODEL, "stream": False},
                        timeout=600,
                    )
                    pull_resp.raise_for_status()
                    print(f"Model {settings.EMBED_MODEL} pulled successfully.")
                else:
                    print(f"Model {settings.EMBED_MODEL} already available.")
    except Exception as e:
        print(f"Warning: could not check/pull Ollama model: {e}")


def _sanitize(text: str) -> str:
    return "".join(ch for ch in text if ch.isprintable() or ch in _WS).strip()


def get_embedding_batch(texts: list[str]) -> list[list[float] | None]:
    """Get embeddings for ALL texts in a SINGLE Ollama API call."""
    clean: list[str] = []
    indices: list[int] = []
    max_chars = 2048

    for i, text in enumerate(texts):
        cleaned = _sanitize(text)
        if not cleaned:
            continue
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rsplit(" ", 1)[0]
        clean.append(cleaned)
        indices.append(i)

    if not clean:
        return [None] * len(texts)

    results: list[list[float] | None] = [None] * len(texts)

    for start in range(0, len(clean), 512):
        batch = clean[start : start + 512]
        batch_indices = indices[start : start + 512]

        try:
            with httpx.Client(timeout=300) as client:
                resp = client.post(
                    f"{settings.OLLAMA_URL}/api/embed",
                    json={
                        "model": settings.EMBED_MODEL,
                        "input": batch,
                    },
                )
                if resp.status_code == 400:
                    print(
                        f"Batch of {len(batch)} rejected (400), falling back to singles"
                    )
                    for idx in batch_indices:
                        results[idx] = _single_embedding(texts[idx])
                    continue

                resp.raise_for_status()
                data = cast(dict[str, Any], resp.json())
                embeddings = cast(list[Any], data.get("embeddings", []))

                for j, emb in enumerate(embeddings):
                    if j < len(batch_indices):
                        results[batch_indices[j]] = cast(list[float], emb)

        except Exception as e:
            print(
                f"Batch embedding failed ({len(batch)} texts): {e}, falling back to singles"
            )
            for idx in batch_indices:
                results[idx] = _single_embedding(texts[idx])

    return results


def _single_embedding(text: str) -> list[float] | None:
    """Get a single embedding (fallback)."""
    cleaned = _sanitize(text)
    if not cleaned:
        return None

    max_chars = 2048
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0]

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{settings.OLLAMA_URL}/api/embed",
                json={"model": settings.EMBED_MODEL, "input": cleaned},
            )
            if resp.status_code == 400:
                retry_text = cleaned[: max_chars // 4]
                if retry_text:
                    resp = client.post(
                        f"{settings.OLLAMA_URL}/api/embed",
                        json={"model": settings.EMBED_MODEL, "input": retry_text},
                    )
            resp.raise_for_status()
            data = cast(dict[str, Any], resp.json())
            embeddings = cast(list[Any], data.get("embeddings", []))
            if embeddings:
                return cast(list[float], embeddings[0])
            return None
    except Exception:
        return None


def embed_with_recursive_bisection(
    text: str,
    max_depth: int = 8,
    min_chunk_size: int = 20,
) -> list[str]:
    """Split a poison chunk recursively until all fragments embed successfully."""
    cleaned = _sanitize(text)
    if not cleaned:
        return []

    embedding = _single_embedding(cleaned)
    if embedding is not None:
        return [cleaned]

    if len(cleaned) <= min_chunk_size or max_depth <= 0:
        return []

    paragraphs = cleaned.split("\n\n")
    if len(paragraphs) > 1 and len(paragraphs) < 20:
        results: list[str] = []
        for para in paragraphs:
            results.extend(
                embed_with_recursive_bisection(para, max_depth - 1, min_chunk_size)
            )
        return results

    mid = len(cleaned) // 2
    left = cleaned[:mid].rsplit(" ", 1)[0] if " " in cleaned[:mid] else cleaned[:mid]
    right = cleaned[len(left) :].lstrip()

    results = []
    results.extend(embed_with_recursive_bisection(left, max_depth - 1, min_chunk_size))
    results.extend(embed_with_recursive_bisection(right, max_depth - 1, min_chunk_size))
    return results
