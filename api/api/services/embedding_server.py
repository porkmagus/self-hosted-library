"""Dedicated embedding server — keeps model permanently loaded on GPU.

Workers send HTTP requests for embeddings and go back to parsing immediately.
Multiple workers all hitting the same server = constant stream = GPU stays pinned.

Uses async + thread pool so multiple concurrent requests keep the GPU fed continuously.
"""

from __future__ import annotations

import asyncio
import io
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import torch
from fastapi import FastAPI, HTTPException, Request
from PIL import Image
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_model: Any = None
_model_name = ""
_clip_model: Any = None
_clip_processor: Any = None
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="embed")
# Semaphore to prevent GPU OOM from too many concurrent requests
_gpu_semaphore = asyncio.Semaphore(4)


def _init_model(model_name: str):
    global _model, _model_name
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_map = {
        "bge-large": "BAAI/bge-large-en-v1.5",
        "bge-small": "BAAI/bge-small-en-v1.5",
    }
    hf_model = model_map.get(model_name, model_name)
    _model_name = model_name

    logger.info(
        "Loading SentenceTransformer '%s' (%s) on %s",
        model_name,
        hf_model,
        device,
    )
    _model = SentenceTransformer(hf_model, device=device)
    logger.info("Embedding model loaded on %s", _model.device)


def _init_clip(model_name: str) -> None:
    global _clip_model, _clip_processor
    from transformers import CLIPModel, CLIPProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading CLIP model '%s' on %s", model_name, device)
    _clip_processor = CLIPProcessor.from_pretrained(model_name)
    _clip_model = CLIPModel.from_pretrained(model_name).to(device)
    _clip_model.eval()
    logger.info("CLIP model loaded on %s", device)


def _do_embed_image(image_bytes: bytes) -> list[float]:
    """Decode and normalize one CLIP image embedding."""
    if _clip_model is None or _clip_processor is None:
        raise RuntimeError("CLIP model not loaded")
    with Image.open(io.BytesIO(image_bytes)) as image:
        inputs = _clip_processor(images=[image.convert("RGB")], return_tensors="pt")
    inputs = {name: value.to(_clip_model.device) for name, value in inputs.items()}
    with torch.inference_mode():
        vision_output = _clip_model.vision_model(**inputs)
        vector = _clip_model.visual_projection(vision_output.pooler_output)
        vector = vector / vector.norm(dim=-1, keepdim=True)
    return [float(value) for value in vector[0].detach().cpu().tolist()]


class EmbedRequest(BaseModel):
    texts: list[str]
    batch_size: int = 256


class EmbedResponse(BaseModel):
    embeddings: list[list[float] | None]


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    model_name = os.environ.get("EMBED_MODEL", "bge-large")
    clip_model_name = os.environ.get("CLIP_MODEL", "openai/clip-vit-base-patch32")
    _init_model(model_name)
    _init_clip(clip_model_name)
    yield
    _executor.shutdown(wait=False)


app = FastAPI(
    title="Embedding Server",
    lifespan=lifespan,
)


def _do_embed(request: EmbedRequest) -> list[list[float] | None]:
    """Run embedding in a thread — releases GIL, allows concurrent GPU work."""
    max_chars = 2048
    cleaned: list[str] = []
    indices: list[int] = []

    for i, text in enumerate(request.texts):
        c = "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")
        c = c.strip()
        if not c:
            continue
        if len(c) > max_chars:
            c = c[:max_chars].rsplit(" ", 1)[0]
        cleaned.append(c)
        indices.append(i)

    results: list[list[float] | None] = [None] * len(request.texts)

    if not cleaned:
        return results

    embeddings = _model.encode(
        cleaned,
        batch_size=request.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    for j, emb in enumerate(embeddings):
        results[indices[j]] = emb.tolist()
    return results


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest) -> EmbedResponse:
    """Get embeddings for a batch of texts. Async so multiple requests can queue."""
    if _model is None:
        raise HTTPException(503, "Model not loaded")

    async with _gpu_semaphore:
        loop = asyncio.get_event_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, lambda: _do_embed(request)),
                timeout=120,
            )
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            raise HTTPException(500, str(e))

    return EmbedResponse(embeddings=results)


@app.post("/embed-image")
async def embed_image(request: Request) -> dict[str, list[float]]:
    """Embed one raw image using the shared GPU-resident CLIP model."""
    if _clip_model is None:
        raise HTTPException(503, "CLIP model not loaded")
    image_bytes = await request.body()
    if not image_bytes:
        raise HTTPException(400, "Empty image body")
    async with _gpu_semaphore:
        loop = asyncio.get_running_loop()
        try:
            vector = await asyncio.wait_for(
                loop.run_in_executor(_executor, _do_embed_image, image_bytes),
                timeout=120,
            )
        except Exception as e:
            logger.exception("CLIP image embedding failed")
            raise HTTPException(422, str(e)) from e
    return {"embedding": vector}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": _model_name,
        "device": str(_model.device) if _model else "not loaded",
        "cuda_available": torch.cuda.is_available(),
        "clip_loaded": _clip_model is not None,
    }
