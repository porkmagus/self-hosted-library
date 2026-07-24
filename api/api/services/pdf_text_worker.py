"""Bounded native-text extraction from an untrusted PDF."""

from __future__ import annotations

import sys
from pathlib import Path

from api.services.pdf_limits import (
    DEFAULT_MAX_CPU_SECONDS,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_MEMORY_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_PAGE_PIXELS,
    DEFAULT_MAX_PAGES,
    DEFAULT_PDF_DPI,
    TERMINAL_DOCUMENT_EXIT_CODE,
    DocumentLimitError,
    apply_resource_limits,
    positive_env,
    validate_document,
)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pdf_text_worker INPUT.pdf OUTPUT.txt", file=sys.stderr)
        return 2
    source, output = map(Path, sys.argv[1:])
    limits = {
        "dpi": positive_env("PDF_DPI", DEFAULT_PDF_DPI),
        "max_input_bytes": positive_env("PDF_MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES),
        "max_pages": positive_env("PDF_MAX_PAGES", DEFAULT_MAX_PAGES),
        "max_page_pixels": positive_env(
            "PDF_MAX_PAGE_PIXELS", DEFAULT_MAX_PAGE_PIXELS
        ),
        "max_output_bytes": positive_env(
            "PDF_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES
        ),
        "max_memory_bytes": positive_env(
            "PDF_MAX_MEMORY_BYTES", DEFAULT_MAX_MEMORY_BYTES
        ),
        "max_cpu_seconds": positive_env(
            "PDF_MAX_CPU_SECONDS", DEFAULT_MAX_CPU_SECONDS
        ),
    }
    apply_resource_limits(
        max_memory_bytes=limits["max_memory_bytes"],
        max_cpu_seconds=limits["max_cpu_seconds"],
        max_output_bytes=limits["max_output_bytes"],
    )
    if source.stat().st_size > limits["max_input_bytes"]:
        raise DocumentLimitError("PDF input limit exceeded")

    import fitz

    document = fitz.open(str(source))
    written = 0
    try:
        validate_document(
            document,
            dpi=limits["dpi"],
            max_pages=limits["max_pages"],
            max_page_pixels=limits["max_page_pixels"],
        )
        with output.open("w", encoding="utf-8") as destination:
            for page_number in range(len(document)):
                page = document.load_page(page_number)
                try:
                    payload = f"{page.get_text('text')}\n\n"
                    written += len(payload.encode("utf-8"))
                    if written > limits["max_output_bytes"]:
                        raise DocumentLimitError("PDF text output limit exceeded")
                    destination.write(payload)
                finally:
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
