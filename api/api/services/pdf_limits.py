"""Shared hard limits for subprocesses that parse untrusted PDFs."""

from __future__ import annotations

import math
import os
import resource
from typing import Any

DEFAULT_PDF_DPI = 150
DEFAULT_MAX_INPUT_BYTES = 536_870_912
DEFAULT_MAX_PAGES = 2_000
DEFAULT_MAX_PAGE_PIXELS = 25_000_000
DEFAULT_MAX_OUTPUT_BYTES = 67_108_864
DEFAULT_MAX_MEMORY_BYTES = 3_221_225_472
DEFAULT_MAX_CPU_SECONDS = 3_600
TERMINAL_DOCUMENT_EXIT_CODE = 42


class DocumentLimitError(ValueError):
    """A deterministic source-document policy limit was exceeded."""


def positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def finite_positive(value: str | float, *, name: str, maximum: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or parsed > maximum:
        raise ValueError(f"{name} must be finite, positive, and at most {maximum:g}")
    return parsed


def pdf_limit_environment() -> dict[str, str]:
    return {
        "PDF_MAX_INPUT_BYTES": str(DEFAULT_MAX_INPUT_BYTES),
        "PDF_MAX_PAGES": str(DEFAULT_MAX_PAGES),
        "PDF_MAX_PAGE_PIXELS": str(DEFAULT_MAX_PAGE_PIXELS),
        "PDF_MAX_OUTPUT_BYTES": str(DEFAULT_MAX_OUTPUT_BYTES),
        "PDF_MAX_MEMORY_BYTES": str(DEFAULT_MAX_MEMORY_BYTES),
        "PDF_MAX_CPU_SECONDS": str(DEFAULT_MAX_CPU_SECONDS),
        "PDF_DPI": str(DEFAULT_PDF_DPI),
    }


def apply_resource_limits(
    *, max_memory_bytes: int, max_cpu_seconds: int, max_output_bytes: int
) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes))


def validate_document(
    document: Any, *, dpi: int, max_pages: int, max_page_pixels: int
) -> None:
    page_count = len(document)
    if page_count > max_pages:
        raise DocumentLimitError(f"PDF page limit exceeded: {page_count} > {max_pages}")
    for page_number in range(page_count):
        page = document.load_page(page_number)
        try:
            width = math.ceil(float(page.rect.width) * dpi / 72)
            height = math.ceil(float(page.rect.height) * dpi / 72)
            pixels = width * height
            if pixels > max_page_pixels:
                raise DocumentLimitError(
                    "PDF page pixel limit exceeded: "
                    f"page={page_number + 1} pixels={pixels} limit={max_page_pixels}"
                )
        finally:
            del page
