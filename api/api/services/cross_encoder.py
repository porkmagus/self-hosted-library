"""Cross-encoder re-ranking service."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_PATH = "/app/models/cross-encoder"
_model: Any | None = None

# Suppress tqdm progress bars from sentence-transformers
os.environ.setdefault("TQDM_DISABLE", "1")


@lru_cache(maxsize=1)
def get_cross_encoder() -> Any | None:
    """Load cross-encoder from local model files."""
    global _model
    if _model is not None:
        return _model

    try:
        from sentence_transformers import CrossEncoder

        logger.info("Loading cross-encoder from local path: %s", _MODEL_PATH)
        _model = CrossEncoder(_MODEL_PATH, max_length=512)
        logger.info("Cross-encoder loaded successfully")
        return _model
    except Exception as e:
        logger.warning("Failed to load cross-encoder: %s — re-ranking disabled", e)
        _model = None
        return None


def rerank_results(
    query: str,
    results: list[dict[str, Any]],
    top_k: int = 20,
    max_candidates: int = 50,
) -> list[dict[str, Any]]:
    """Re-rank search results using cross-encoder scoring."""
    if not results or not query.strip():
        return results

    model = get_cross_encoder()
    if model is None:
        return results[:top_k]

    candidates = results[:max_candidates]
    if not candidates:
        return []

    pairs = [[query, r.get("text", "")] for r in candidates]

    try:
        import numpy as np

        logits = model.predict(pairs)
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -500, 500)))

        for i, prob in enumerate(probs):
            candidates[i]["score"] = round(float(prob), 4)

        candidates.sort(key=lambda r: float(r["score"]), reverse=True)
        return candidates[:top_k]

    except Exception as e:
        logger.warning("Cross-encoder scoring failed: %s", e)
        return results[:top_k]


_available: bool | None = None


def cross_encoder_available() -> bool:
    """Check if cross-encoder is loaded — cached after first call."""
    global _available
    if _available is not None:
        return _available
    model = get_cross_encoder()
    _available = model is not None
    return _available
