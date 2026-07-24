"""Bounded extraction of displayed images from an untrusted PDF."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from api.services.pdf_limits import (
    DEFAULT_MAX_CPU_SECONDS,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_MEMORY_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES,
    TERMINAL_DOCUMENT_EXIT_CODE,
    DocumentLimitError,
    apply_resource_limits,
    positive_env,
)


def main() -> int:
    if len(sys.argv) != 6:
        print(
            "usage: pdf_image_worker INPUT.pdf OUTPUT_DIR BOOK_ID TITLE INCLUDE_FULL_PAGE",
            file=sys.stderr,
        )
        return 2
    source = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    book_id = sys.argv[3]
    title = sys.argv[4]
    include_full_page = sys.argv[5] == "1"
    max_input = positive_env("PDF_MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES)
    max_output = positive_env("PDF_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)
    apply_resource_limits(
        max_memory_bytes=positive_env("PDF_MAX_MEMORY_BYTES", DEFAULT_MAX_MEMORY_BYTES),
        max_cpu_seconds=positive_env("PDF_MAX_CPU_SECONDS", DEFAULT_MAX_CPU_SECONDS),
        max_output_bytes=max_output,
    )
    if source.stat().st_size > max_input:
        raise DocumentLimitError("PDF input limit exceeded")

    from api.services.image_svc import _extract_images_in_process

    output_dir.mkdir(parents=True, exist_ok=True)
    images = _extract_images_in_process(
        str(source),
        book_id,
        title,
        include_full_page=include_full_page,
    )
    metadata = []
    written = 0
    for index, image in enumerate(images):
        payload = image.pop("image_bytes")
        written += len(payload)
        if written > max_output:
            raise DocumentLimitError("PDF image cumulative output limit exceeded")
        filename = f"image-{index:05d}.{image['ext']}"
        (output_dir / filename).write_bytes(payload)
        metadata.append({**image, "filename": filename})
    manifest = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    if written + len(manifest) > max_output:
        raise DocumentLimitError("PDF image manifest output limit exceeded")
    (output_dir / "manifest.json").write_bytes(manifest)
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
