"""GPU-backed OCR server.

Keeps an EasyOCR reader resident on the GPU. Workers upload PDFs (or page images)
over HTTP and receive extracted text. This moves OCR off the CPU-bound worker
containers and onto the same GPU that already runs embeddings, keeping both CPU
and GPU fed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_reader: Any = None
# Allow operator to cap CPU burn. Default to a small pool so search stays alive.
_ocr_concurrency = int(os.environ.get("OCR_CONCURRENCY", 2))
_executor = ThreadPoolExecutor(max_workers=max(1, _ocr_concurrency), thread_name_prefix="ocr")
_gpu_semaphore = asyncio.Semaphore(_ocr_concurrency)


def _init_ocr() -> None:
    global _reader
    import easyocr

    # Default to CPU so the GPU stays reserved for the embedding model. EasyOCR on
    # CPU is still substantially faster than the legacy serialized Tesseract path.
    use_gpu = os.environ.get("OCR_USE_GPU", "false").lower() in ("1", "true", "yes")
    if use_gpu and not torch.cuda.is_available():
        logger.warning("OCR_USE_GPU=true but CUDA unavailable; falling back to CPU")
        use_gpu = False
    device_name = torch.cuda.get_device_name(0) if use_gpu else "cpu"
    logger.info("Loading EasyOCR (lang=en, gpu=%s: %s)", use_gpu, device_name)
    _reader = easyocr.Reader(
        ["en"],
        gpu=use_gpu,
        model_storage_directory="/app/models/easyocr",
        download_enabled=True,
    )
    logger.info("EasyOCR loaded on %s", device_name if use_gpu else "cpu")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_ocr()
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="OCR Server", lifespan=lifespan)


class OCRPageImage(BaseModel):
    image_b64: str
    page_number: int = 0


class OCRRequest(BaseModel):
    images: list[OCRPageImage]


class OCRResponse(BaseModel):
    text: str
    pages: int


def _pil_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _ocr_image_bytes(image_bytes: bytes) -> str:
    """Run EasyOCR on raw image bytes and return joined text lines."""
    if _reader is None:
        raise RuntimeError("OCR reader not loaded")
    # EasyOCR accepts numpy arrays directly; decode via PIL and convert.
    with Image.open(io.BytesIO(image_bytes)) as img:
        img_rgb = img.convert("RGB")
        arr = np.array(img_rgb)
        results = _reader.readtext(arr, detail=0, paragraph=False)
    return "\n".join(results)


def _ocr_pdf_bytes(pdf_bytes: bytes, dpi: int = 150, max_pages: int = 2000) -> tuple[str, int]:
    """Render PDF pages to images and OCR each one on the GPU."""
    text_parts: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = min(len(doc), max_pages)
        for page_number in range(page_count):
            page = doc.load_page(page_number)
            try:
                pix = page.get_pixmap(dpi=dpi)
                image_bytes = pix.tobytes("png")
                page_text = _ocr_image_bytes(image_bytes)
                if page_text.strip():
                    text_parts.append(page_text)
            finally:
                del page
    return "\n\n".join(text_parts), len(text_parts)


@app.post("/ocr/pdf", response_model=OCRResponse)
async def ocr_pdf(
    file: UploadFile = File(...),
    dpi: int = Form(150),
    max_pages: int = Form(2000),
) -> OCRResponse:
    """OCR an uploaded PDF. Returns extracted text and page count."""
    if _reader is None:
        raise HTTPException(503, "OCR reader not loaded")
    contents = await file.read()
    if not contents:
        raise HTTPException(400, "Empty PDF body")

    async with _gpu_semaphore:
        loop = asyncio.get_running_loop()
        try:
            text, pages = await asyncio.wait_for(
                loop.run_in_executor(_executor, _ocr_pdf_bytes, contents, dpi, max_pages),
                timeout=600,
            )
        except Exception as e:
            logger.exception("PDF OCR failed")
            raise HTTPException(500, str(e)) from e

    return OCRResponse(text=text, pages=pages)


@app.post("/ocr/images", response_model=OCRResponse)
async def ocr_images(request: OCRRequest) -> OCRResponse:
    """OCR a batch of base64-encoded page images."""
    if _reader is None:
        raise HTTPException(503, "OCR reader not loaded")
    if not request.images:
        raise HTTPException(400, "No images provided")

    texts: list[str] = []
    async with _gpu_semaphore:
        loop = asyncio.get_running_loop()
        for item in request.images:
            try:
                image_bytes = base64.b64decode(item.image_b64)
            except Exception as e:
                logger.warning("Invalid base64 for page %s: %s", item.page_number, e)
                continue
            try:
                page_text = await asyncio.wait_for(
                    loop.run_in_executor(_executor, _ocr_image_bytes, image_bytes),
                    timeout=120,
                )
                if page_text.strip():
                    texts.append(page_text)
            except Exception as e:
                logger.warning("OCR failed for page %s: %s", item.page_number, e)

    return OCRResponse(text="\n\n".join(texts), pages=len(texts))


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "reader_loaded": _reader is not None,
    }
