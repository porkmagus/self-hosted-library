"""Provider-neutral access to the application's private S3-compatible store."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from minio import Minio
from minio.error import S3Error

from api.config import settings

_store: S3ObjectStore | None = None


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    etag: str
    content_type: str | None


class S3ObjectStore:
    """Small S3 contract shared by uploads, workers, viewers, and health checks."""

    def __init__(self, *, client: Any, bucket: str) -> None:
        self._client = client
        self.bucket = bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def bucket_exists(self) -> bool:
        return bool(self._client.bucket_exists(self.bucket))

    def upload_stream(
        self,
        object_name: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        self._client.put_object(
            bucket_name=self.bucket,
            object_name=object_name,
            data=data,
            length=length,
            content_type=content_type,
        )

    def download_to(self, object_name: str, destination: Path) -> None:
        self._client.fget_object(self.bucket, object_name, str(destination))

    def iter_bytes(
        self, object_name: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Yield a private object while always releasing the HTTP connection."""
        response = self._client.get_object(self.bucket, object_name)
        try:
            yield from response.stream(chunk_size)
        finally:
            response.close()
            response.release_conn()

    def stat(self, object_name: str) -> ObjectInfo:
        result = self._client.stat_object(self.bucket, object_name)
        return ObjectInfo(
            size=int(result.size),
            etag=str(result.etag),
            content_type=result.content_type,
        )

    def exists(self, object_name: str) -> bool:
        try:
            self._client.stat_object(self.bucket, object_name)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                return False
            raise
        return True

    def delete(self, object_name: str) -> None:
        self._client.remove_object(self.bucket, object_name)

    def iter_keys(self, prefix: str) -> Iterator[str]:
        """Enumerate every private object under a deterministic application prefix."""
        for item in self._client.list_objects(
            self.bucket, prefix=prefix, recursive=True
        ):
            yield str(item.object_name)


def get_object_store() -> S3ObjectStore:
    """Return the process-local private object-store adapter."""
    global _store
    if _store is None:
        client = Minio(
            settings.object_store_endpoint,
            access_key=settings.object_store_access_key,
            secret_key=settings.object_store_secret_key,
            secure=False,
        )
        _store = S3ObjectStore(client=client, bucket=settings.object_store_bucket)
    return _store
