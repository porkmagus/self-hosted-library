import io
import resource
from types import SimpleNamespace

import pytest

from api.services.pdf_limits import DocumentLimitError
from api.services.pdf_ocr_worker import (
    apply_resource_limits,
    validate_document,
    write_page_text,
)


class FakeDocument:
    def __init__(self, page_sizes: list[tuple[float, float]]) -> None:
        self._page_sizes = page_sizes

    def __len__(self) -> int:
        return len(self._page_sizes)

    def load_page(self, index: int):
        width, height = self._page_sizes[index]
        return SimpleNamespace(rect=SimpleNamespace(width=width, height=height))


def test_ocr_rejects_documents_over_page_limit() -> None:
    document = FakeDocument([(612, 792), (612, 792)])

    with pytest.raises(DocumentLimitError, match="page limit"):
        validate_document(document, dpi=150, max_pages=1, max_page_pixels=25_000_000)


def test_ocr_rejects_pages_over_raster_pixel_limit() -> None:
    document = FakeDocument([(10_000, 10_000)])

    with pytest.raises(DocumentLimitError, match="pixel limit"):
        validate_document(document, dpi=150, max_pages=10, max_page_pixels=25_000_000)


def test_ocr_rejects_output_over_byte_limit() -> None:
    destination = io.StringIO()

    with pytest.raises(DocumentLimitError, match="output limit"):
        write_page_text(destination, "too large", written_bytes=0, max_output_bytes=4)


def test_ocr_installs_hard_process_resource_limits(monkeypatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, limit: calls.append((kind, limit)))

    apply_resource_limits(
        max_memory_bytes=3_221_225_472,
        max_cpu_seconds=3600,
        max_output_bytes=67_108_864,
    )

    assert (resource.RLIMIT_AS, (3_221_225_472, 3_221_225_472)) in calls
    assert (resource.RLIMIT_CPU, (3600, 3600)) in calls
    assert (resource.RLIMIT_FSIZE, (67_108_864, 67_108_864)) in calls
