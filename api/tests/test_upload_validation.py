import pytest
from pydantic import ValidationError

from api.routers.upload import UploadRequest


@pytest.mark.parametrize(
    "filename", ["", "../book.pdf", "folder/book.pdf", "folder\\book.pdf"]
)
def test_upload_request_rejects_path_like_filenames(filename: str) -> None:
    with pytest.raises(ValidationError):
        UploadRequest(filename=filename)


def test_upload_request_accepts_plain_filename() -> None:
    request = UploadRequest(filename="My Book.pdf", content_type="application/pdf")
    assert request.filename == "My Book.pdf"
