from pathlib import Path

import fitz
from docx import Document

from api.services.parser_svc import (
    extract_docx,
    extract_pdf_pymupdf,
    extract_text_file,
)

GOLDEN_TEXT = "The moon is a mirror; memory is the silver behind it."


def test_pdf_text_extraction_preserves_searchable_content(tmp_path: Path) -> None:
    path = tmp_path / "golden.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), GOLDEN_TEXT)
    document.save(path)
    document.close()

    assert GOLDEN_TEXT in extract_pdf_pymupdf(path)


def test_docx_text_extraction_preserves_paragraph_order(tmp_path: Path) -> None:
    path = tmp_path / "golden.docx"
    document = Document()
    document.add_paragraph("First invocation")
    document.add_paragraph("Second invocation")
    document.save(str(path))

    assert extract_docx(path) == "First invocation\n\nSecond invocation"


def test_text_extraction_falls_back_to_legacy_encoding(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes("Caf\u00e9 grimoires".encode("cp1252"))

    assert extract_text_file(path) == "Caf\u00e9 grimoires"
