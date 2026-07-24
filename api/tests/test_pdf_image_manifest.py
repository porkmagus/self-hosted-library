import pytest

from api.services.image_svc import _validate_pdf_image_manifest

VALID_ITEM = {
    "filename": "image-00000.png",
    "image_id": "11111111-1111-1111-1111-111111111111",
    "image_sha256": "a" * 64,
    "ext": "png",
    "page_number": 0,
    "width": 64,
    "height": 64,
    "book_id": "book-id",
    "book_title": "Book",
}


def test_pdf_image_manifest_accepts_exact_parent_owned_schema() -> None:
    assert _validate_pdf_image_manifest(
        [dict(VALID_ITEM)], book_id="book-id", book_title="Book"
    ) == [dict(VALID_ITEM)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filename", "../secret"),
        ("filename", "image-99999.png"),
        ("image_id", "../namespace"),
        ("image_sha256", "not-a-sha"),
        ("ext", "../../txt"),
        ("page_number", -1),
        ("width", 0),
        ("height", 100_000_000),
        ("book_id", "another-book"),
        ("book_title", "Another title"),
    ],
)
def test_pdf_image_manifest_rejects_untrusted_fields(field: str, value: object) -> None:
    item = dict(VALID_ITEM)
    item[field] = value

    with pytest.raises(RuntimeError, match="Invalid PDF image manifest"):
        _validate_pdf_image_manifest([item], book_id="book-id", book_title="Book")


def test_pdf_image_manifest_rejects_duplicate_image_ids() -> None:
    with pytest.raises(RuntimeError, match="duplicate"):
        _validate_pdf_image_manifest(
            [dict(VALID_ITEM), {**VALID_ITEM, "filename": "image-00001.png"}],
            book_id="book-id",
            book_title="Book",
        )
