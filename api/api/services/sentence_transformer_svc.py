"""SentenceTransformer embedding service — runs directly on GPU via CUDA."""

from __future__ import annotations

import logging
from typing import List

from api.config import settings

logger = logging.getLogger(__name__)

_model = None
_model_lock = None  # Simple lazy lock; workers are prefork so safe at import time


def _get_model():
    global _model
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        # Map Ollama model names to HuggingFace repo IDs
        model_map = {
            "bge-large": "BAAI/bge-large-en-v1.5",
            "bge-small": "BAAI/bge-small-en-v1.5",
        }
        hf_model = model_map.get(settings.EMBED_MODEL, settings.EMBED_MODEL)
        logger.info(
            "Loading SentenceTransformer model '%s' (%s) on %s",
            settings.EMBED_MODEL,
            hf_model,
            device,
        )
        _model = SentenceTransformer(hf_model, device=device)
    return _model


def get_embedding_batch(texts: list[str]) -> list[list[float] | None]:
    """Get embeddings directly via sentence-transformers on GPU/CPU."""
    if not texts:
        return []

    model = _get_model()

    # Sanitize: strip non-printable, truncate
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

    try:
        embeddings = model.encode(
            cleaned,
            batch_size=256,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        for j, emb in enumerate(embeddings):
            results[indices[j]] = emb.tolist()
    except Exception as e:
        logger.error("SentenceTransformer batch failed: %s", e)

    return results
