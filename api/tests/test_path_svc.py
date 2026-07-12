from pathlib import Path

import pytest

from api.services.path_svc import resolve_under, validate_filename


def test_resolve_under_returns_resolved_child(tmp_path: Path) -> None:
    child = resolve_under(tmp_path, "folder/book.pdf")
    assert child == (tmp_path / "folder/book.pdf").resolve()


@pytest.mark.parametrize("candidate", ["../secret", "folder/../../secret", "/etc/passwd"])
def test_resolve_under_rejects_paths_outside_base(
    tmp_path: Path, candidate: str
) -> None:
    with pytest.raises(ValueError, match="outside"):
        resolve_under(tmp_path, candidate)


def test_resolve_under_rejects_sibling_prefix_attack(tmp_path: Path) -> None:
    base = tmp_path / "data"
    sibling = tmp_path / "data-private" / "secret.pdf"
    with pytest.raises(ValueError, match="outside"):
        resolve_under(base, sibling)


@pytest.mark.parametrize("filename", ["", ".", "..", "folder/book.pdf", "folder\\book.pdf", "bad\x00name.pdf"])
def test_validate_filename_rejects_unsafe_names(filename: str) -> None:
    with pytest.raises(ValueError, match="filename"):
        validate_filename(filename)


def test_validate_filename_accepts_plain_filename() -> None:
    assert validate_filename("My Book.pdf") == "My Book.pdf"
