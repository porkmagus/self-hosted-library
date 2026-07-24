import os
from pathlib import Path

import pytest

from api.services.safe_artifacts import read_bounded_regular_file


def test_bounded_artifact_reader_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    target.write_bytes(b"secret")
    link = tmp_path / "artifact"
    link.symlink_to(target)

    with pytest.raises(OSError, match=r"symlink|regular artifact"):
        read_bounded_regular_file(link, max_bytes=64)


def test_bounded_artifact_reader_rejects_oversize_before_read(
    tmp_path: Path, monkeypatch
) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"x" * 65)
    original_read = os.read
    reads = 0

    def counted_read(fd: int, size: int) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(fd, size)

    monkeypatch.setattr(os, "read", counted_read)

    with pytest.raises(OSError, match="size limit"):
        read_bounded_regular_file(artifact, max_bytes=64)

    assert reads == 0


def test_bounded_artifact_reader_returns_regular_file(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"bounded")

    assert read_bounded_regular_file(artifact, max_bytes=64) == b"bounded"
