"""Media router - browse and stream non-ingestable media files from the data directory."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from api.config import settings
from api.services.path_svc import resolve_under

router = APIRouter()

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma", ".aac"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
ALL_EXTS = AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS | ARCHIVE_EXTS

MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wma": "audio/x-ms-wma",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".zip": "application/zip",
    ".rar": "application/x-rar-compressed",
    ".7z": "application/x-7z-compressed",
}


def _get_media_type(ext: str) -> str:
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    return "other"


def _scan_media(
    base_dir: str,
    type_filter: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    base = Path(base_dir)
    if not base.is_dir():
        return {
            "files": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
        }

    files: list[dict[str, Any]] = []
    for root, _, filenames in os.walk(base):
        for fname in filenames:
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()
            if ext not in ALL_EXTS:
                continue
            mt = _get_media_type(ext)
            if type_filter and mt != type_filter:
                continue
            rel = fpath.relative_to(base)
            parts = list(rel.parts[:-1])
            files.append(
                {
                    "name": fname,
                    "path": str(rel),
                    "type": mt,
                    "extension": ext.lstrip("."),
                    "size": fpath.stat().st_size,
                    "directory": "/".join(parts) if parts else "root",
                    "display_name": "/".join(parts[-2:])
                    if len(parts) >= 2
                    else (parts[0] if parts else "root"),
                }
            )

    files.sort(key=lambda f: (str(f["directory"]), str(f["name"])))
    total = len(files)
    return {
        "files": files[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }


@router.get("/list")
def list_media(
    type: str = Query("all", description="Filter: audio, video, image, archive, all"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    tf = type if type != "all" else None
    return _scan_media(settings.DATA_DIR, tf, limit, offset)


@router.get("/stream")
def stream_media(
    path: str = Query(..., description="Relative path in data dir"),
) -> FileResponse:
    try:
        target = resolve_under(settings.DATA_DIR, path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    ext = target.suffix.lower()
    ct = MIME_MAP.get(
        ext, mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    )
    return FileResponse(path=str(target), media_type=ct, filename=target.name)
