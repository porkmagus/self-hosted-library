"""Resource-bounded, page-at-a-time PDF OCR helper."""

from __future__ import annotations

import math
import os
import resource
import sys
from pathlib import Path
from typing import Any, TextIO

from api.services.pdf_limits import TERMINAL_DOCUMENT_EXIT_CODE, DocumentLimitError


def apply_resource_limits(
    *, max_memory_bytes: int, max_cpu_seconds: int, max_output_bytes: int
) -> None:
    """Install hard OS limits before importing or opening untrusted PDF data."""
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes))


def validate_document(
    document: Any, *, dpi: int, max_pages: int, max_page_pixels: int
) -> None:
    page_count = len(document)
    if page_count > max_pages:
        raise DocumentLimitError(
            f"PDF OCR page limit exceeded: {page_count} > {max_pages}"
        )
    for page_number in range(page_count):
        page = document.load_page(page_number)
        try:
            width = math.ceil(float(page.rect.width) * dpi / 72)
            height = math.ceil(float(page.rect.height) * dpi / 72)
            pixels = width * height
            if pixels > max_page_pixels:
                raise DocumentLimitError(
                    "PDF OCR page pixel limit exceeded: "
                    f"page={page_number + 1} pixels={pixels} limit={max_page_pixels}"
                )
        finally:
            del page


def write_page_text(
    destination: TextIO,
    text: str,
    *,
    written_bytes: int,
    max_output_bytes: int,
) -> int:
    payload = f"{text}\n\n"
    new_total = written_bytes + len(payload.encode("utf-8"))
    if new_total > max_output_bytes:
        raise DocumentLimitError(
            f"PDF OCR output limit exceeded: {new_total} > {max_output_bytes} bytes"
        )
    destination.write(payload)
    return new_total


def _positive_env(name: str) -> int:
    value = int(os.environ[name])
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pdf_ocr_worker INPUT.pdf OUTPUT.txt", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])

    dpi = _positive_env("PDF_OCR_DPI")
    max_input_bytes = _positive_env("PDF_OCR_MAX_INPUT_BYTES")
    max_pages = _positive_env("PDF_OCR_MAX_PAGES")
    max_page_pixels = _positive_env("PDF_OCR_MAX_PAGE_PIXELS")
    max_output_bytes = _positive_env("PDF_OCR_MAX_OUTPUT_BYTES")
    max_memory_bytes = _positive_env("PDF_OCR_MAX_MEMORY_BYTES")
    max_cpu_seconds = _positive_env("PDF_OCR_MAX_CPU_SECONDS")

    apply_resource_limits(
        max_memory_bytes=max_memory_bytes,
        max_cpu_seconds=max_cpu_seconds,
        max_output_bytes=max_output_bytes,
    )
    input_bytes = source.stat().st_size
    if input_bytes > max_input_bytes:
        raise DocumentLimitError(
            f"PDF OCR input limit exceeded: {input_bytes} > {max_input_bytes} bytes"
        )

    import fitz

    document = fitz.open(str(source))
    try:
        validate_document(
            document,
            dpi=dpi,
            max_pages=max_pages,
            max_page_pixels=max_page_pixels,
        )
        written_bytes = 0
        with output.open("w", encoding="utf-8") as destination:
            for page_number in range(len(document)):
                page = document.load_page(page_number)
                textpage = page.get_textpage_ocr(language="eng", dpi=dpi, full=True)
                try:
                    written_bytes = write_page_text(
                        destination,
                        page.get_text("text", textpage=textpage),
                        written_bytes=written_bytes,
                        max_output_bytes=max_output_bytes,
                    )
                finally:
                    del textpage
                    del page
    finally:
        document.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DocumentLimitError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(TERMINAL_DOCUMENT_EXIT_CODE) from exc
    except Exception as exc:
        import fitz

        if isinstance(exc, (fitz.FileDataError, fitz.EmptyFileError)):
            print(str(exc), file=sys.stderr)
            raise SystemExit(TERMINAL_DOCUMENT_EXIT_CODE) from exc
        raise
