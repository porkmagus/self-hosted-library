"""Safe filesystem and object-name validation helpers."""

from __future__ import annotations

from os import PathLike
from pathlib import Path


def resolve_under(base_dir: str | PathLike[str], candidate: str | PathLike[str]) -> Path:
    """Resolve a relative path and reject escapes outside ``base_dir``."""
    base = Path(base_dir).resolve()
    raw = Path(candidate)
    target = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
    if not target.is_relative_to(base):
        raise ValueError("path resolves outside the configured data directory")
    return target


def validate_filename(filename: str) -> str:
    """Accept a plain filename, never a path or control-character payload."""
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or len(filename) > 255
    ):
        raise ValueError("invalid filename")
    return filename
