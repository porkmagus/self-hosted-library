"""Cross-encoder re-ranking service using Mixedbread mxbai-rerank-base-v2."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_PATH = "/app/models/cross-encoder"
_model: Any | None = None

# Suppress tqdm progress bars
os.environ.setdefault("TQDM_DISABLE", "1")


@lru_cache(maxsize=1)
def get_cross_encoder() -> Any | None:
    """Load mxbai-rerank-base-v2 from local model files."""
    global _model
    if _model is not None:
        return _model

    try:
        from mxbai_rerank import MxbaiRerankV2
        from transformers import PreTrainedTokenizerBase

        logger.info("Loading mxbai-rerank-base-v2 from local path: %s", _MODEL_PATH)
        _model = MxbaiRerankV2(_MODEL_PATH, max_length=8192)

        # Transformers 5.x removed prepare_for_model from Qwen2Tokenizer.
        # Monkey-patch a shim so mxbai-rerank v0.1.6 continues to work.
        if not hasattr(_model.tokenizer, "prepare_for_model"):
            def _prepare_for_model_shim(
                self,
                ids,
                pair_ids=None,
                truncation="only_second",
                max_length=None,
                padding=False,
                return_attention_mask=False,
                return_token_type_ids=False,
                add_special_tokens=False,
            ):
                combined = list(ids)
                if pair_ids is not None:
                    combined = list(ids) + list(pair_ids)
                if max_length is not None and max_length > 0:
                    combined = combined[:max_length]
                return {"input_ids": combined}

            _model.tokenizer.prepare_for_model = _prepare_for_model_shim.__get__(
                _model.tokenizer, type(_model.tokenizer)
            )

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
    """Re-rank search results using mxbai-rerank-base-v2 scoring."""
    if not results or not query.strip():
        return results

    model = get_cross_encoder()
    if model is None:
        return results[:top_k]

    candidates = results[:max_candidates]
    if not candidates:
        return []

    documents = [r.get("text", "") for r in candidates]

    try:
        ranked = model.rank(query=query, documents=documents, top_k=top_k)
        # ranked is a list of RankResult dataclasses (.index, .score, .document)
        ordered: list[dict[str, Any]] = []
        for item in ranked:
            idx = item.index
            if idx < len(candidates):
                candidates[idx]["score"] = round(float(item.score), 4)
                ordered.append(candidates[idx])
        return ordered[:top_k]

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
