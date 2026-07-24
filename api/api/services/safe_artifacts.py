"""Safe handoff of bounded artifacts produced by untrusted parser children."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    """Read one fixed artifact without following links or allocating past its cap."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OSError(f"Artifact is a symlink or cannot be opened: {path.name}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"Artifact is not a regular artifact: {path.name}")
        if info.st_size > max_bytes:
            raise OSError(f"Artifact exceeds size limit: {path.name}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise OSError(f"Artifact exceeds size limit: {path.name}")
        return payload
    finally:
        os.close(fd)


def read_regular_prefix(path: Path, *, max_bytes: int) -> bytes:
    """Read only a fixed prefix from a no-follow regular file of any total size."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OSError(f"Source is a symlink or cannot be opened: {path.name}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"Source is not a regular file: {path.name}")
        return os.read(fd, max_bytes)
    finally:
        os.close(fd)
