import io
from pathlib import Path

import fitz
from PIL import Image

from api.services.image_svc import extract_images_from_pdf


def _write_pdf_with_image(path: Path) -> None:
    image = Image.new("RGB", (64, 48), color=(44, 18, 77))
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
    assert images[0]["height"] == 48
    assert images[0]["image_bytes"]


def test_pdf_image_identity_is_stable_for_repeated_extraction(tmp_path: Path) -> None:
    path = tmp_path / "illustrated.pdf"
    _write_pdf_with_image(path)

    first = extract_images_from_pdf(str(path), "book-1", "Illustrated Grimoire")
    second = extract_images_from_pdf(str(path), "book-1", "Illustrated Grimoire")

    assert first[0]["image_id"] == second[0]["image_id"]
