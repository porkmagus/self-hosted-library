import io
import shutil
from pathlib import Path

import fitz
import pytest
from PIL import Image

from api.services import image_svc
from api.services.image_svc import ImageDocumentError, extract_images_from_pdf


def _write_pdf_with_image(path: Path) -> None:
    image = Image.new("RGB", (64, 64), color=(44, 18, 77))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    document = fitz.open()
    page = document.new_page()
    page.insert_image(fitz.Rect(72, 72, 200, 168), stream=buffer.getvalue())
    document.save(path)
    document.close()


def test_pdf_image_extraction_preserves_metadata_and_bytes(tmp_path: Path) -> None:
    path = tmp_path / "illustrated.pdf"
    _write_pdf_with_image(path)

    images = extract_images_from_pdf(str(path), "book-1", "Illustrated Grimoire")

    assert len(images) == 1
    assert images[0]["book_id"] == "book-1"
    assert images[0]["book_title"] == "Illustrated Grimoire"
    assert images[0]["page_number"] == 0
    assert images[0]["width"] == 64
    assert images[0]["height"] == 64
    assert images[0]["image_bytes"]


def test_pdf_image_identity_is_stable_for_repeated_extraction(tmp_path: Path) -> None:
    path = tmp_path / "illustrated.pdf"
    _write_pdf_with_image(path)

    first = extract_images_from_pdf(str(path), "book-1", "Illustrated Grimoire")
    second = extract_images_from_pdf(str(path), "book-1", "Illustrated Grimoire")

    assert first[0]["image_id"] == second[0]["image_id"]


def test_pdf_image_identity_is_independent_of_worker_temp_path(tmp_path: Path) -> None:
    first_path = tmp_path / "worker-a" / "source.pdf"
    second_path = tmp_path / "worker-b" / "renamed.pdf"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    _write_pdf_with_image(first_path)
    shutil.copyfile(first_path, second_path)

    first = extract_images_from_pdf(str(first_path), "book-1", "Illustrated")
    second = extract_images_from_pdf(str(second_path), "book-1", "Illustrated")

    assert first[0]["image_id"] == second[0]["image_id"]


def test_corrupt_pdf_image_extraction_is_not_silently_treated_as_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a pdf")

    with pytest.raises(ImageDocumentError, match="Failed to open file"):
        extract_images_from_pdf(str(path), "book-1", "Corrupt")


def test_extracted_images_are_normalized_to_streamable_png() -> None:
    source = io.BytesIO()
    Image.new("RGB", (12, 12), "purple").save(source, format="BMP")

    payload, extension = image_svc.normalize_image_bytes(source.getvalue())

    assert extension == "png"
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
