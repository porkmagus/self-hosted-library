import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from minio.error import S3Error

from api.services import object_store
from api.services.object_store import S3ObjectStore


class FakeS3Client:
    def __init__(
        self,
        *,
        bucket_exists: bool = False,
        stat_error: Exception | None = None,
    ) -> None:
        self.has_bucket = bucket_exists
        self.stat_error = stat_error
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def bucket_exists(self, bucket: str) -> bool:
        self.calls.append(("bucket_exists", (bucket,), {}))
        return self.has_bucket

    def make_bucket(self, bucket: str) -> None:
        self.calls.append(("make_bucket", (bucket,), {}))
        self.has_bucket = True

    def put_object(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("put_object", args, kwargs))

    def fget_object(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("fget_object", args, kwargs))

    def stat_object(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("stat_object", args, kwargs))
        if self.stat_error is not None:
            raise self.stat_error
        return SimpleNamespace(size=17, etag="etag-1", content_type="text/plain")

    def remove_object(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("remove_object", args, kwargs))

    def list_objects(self, *args: Any, **kwargs: Any):
        self.calls.append(("list_objects", args, kwargs))
        yield SimpleNamespace(object_name="books/book-1/generations/1/images/a.png")
        yield SimpleNamespace(object_name="books/book-1/generations/1/images/b.png")

    def get_object(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("get_object", args, kwargs))

        class Response:
            closed = False
            released = False

            def stream(self, chunk_size: int):
                assert chunk_size == 4
                yield b"data"

            def close(self) -> None:
                self.closed = True

            def release_conn(self) -> None:
                self.released = True

        self.response = Response()
        return self.response


def test_ensure_bucket_creates_only_when_missing() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client=client, bucket="library-files")

    store.ensure_bucket()
    store.ensure_bucket()

    assert [call[0] for call in client.calls] == [
        "bucket_exists",
        "make_bucket",
        "bucket_exists",
    ]


def test_upload_stream_does_not_buffer_or_replace_the_stream() -> None:
    client = FakeS3Client(bucket_exists=True)
    store = S3ObjectStore(client=client, bucket="library-files")
    stream = io.BytesIO(b"streamed document")

    store.upload_stream(
        "books/book-1/source.pdf",
        stream,
        length=17,
        content_type="application/pdf",
    )

    _, args, kwargs = client.calls[-1]
    assert not args
    assert kwargs == {
        "bucket_name": "library-files",
        "object_name": "books/book-1/source.pdf",
        "data": stream,
        "length": 17,
        "content_type": "application/pdf",
    }


def test_download_stat_exists_and_delete_use_private_bucket(tmp_path: Path) -> None:
    client = FakeS3Client(bucket_exists=True)
    store = S3ObjectStore(client=client, bucket="library-files")
    destination = tmp_path / "source.pdf"

    store.download_to("books/book-1/source.pdf", destination)
    info = store.stat("books/book-1/source.pdf")
    assert store.exists("books/book-1/source.pdf") is True
    store.delete("books/book-1/source.pdf")

    assert info.size == 17
    assert info.etag == "etag-1"
    assert info.content_type == "text/plain"
    assert (
        "fget_object",
        ("library-files", "books/book-1/source.pdf", str(destination)),
        {},
    ) in client.calls
    assert (
        "remove_object",
        ("library-files", "books/book-1/source.pdf"),
        {},
    ) in client.calls


def test_iter_bytes_releases_the_storage_connection() -> None:
    client = FakeS3Client(bucket_exists=True)
    store = S3ObjectStore(client=client, bucket="library-files")

    assert (
        b"".join(store.iter_bytes("books/book-1/source.pdf", chunk_size=4)) == b"data"
    )
    assert client.response.closed is True
    assert client.response.released is True


def test_iter_keys_lists_private_prefix_recursively() -> None:
    client = FakeS3Client(bucket_exists=True)
    store = S3ObjectStore(client=client, bucket="library-files")

    assert list(store.iter_keys("books/book-1/generations/")) == [
        "books/book-1/generations/1/images/a.png",
        "books/book-1/generations/1/images/b.png",
    ]
    assert client.calls[-1] == (
        "list_objects",
        ("library-files",),
        {"prefix": "books/book-1/generations/", "recursive": True},
    )


def test_exists_returns_false_only_for_missing_objects() -> None:
    missing = S3Error(
        None,
        "NoSuchKey",
        "missing",
        "books/book-1/source.pdf",
        "request-id",
        "host-id",
        None,
    )
    store = S3ObjectStore(
        client=FakeS3Client(bucket_exists=True, stat_error=missing),
        bucket="library-files",
    )

    assert store.exists("books/book-1/source.pdf") is False


def test_get_object_store_builds_one_private_client(monkeypatch) -> None:
    created: list[dict[str, Any]] = []

    def make_client(endpoint: str, **kwargs: Any) -> FakeS3Client:
        created.append({"endpoint": endpoint, **kwargs})
        return FakeS3Client(bucket_exists=True)

    monkeypatch.setattr(object_store, "Minio", make_client)
    monkeypatch.setattr(object_store.settings, "S3_ENDPOINT", "seaweedfs:8333")
    monkeypatch.setattr(object_store.settings, "S3_ACCESS_KEY", "access")
    monkeypatch.setattr(object_store.settings, "S3_SECRET_KEY", "secret")
    monkeypatch.setattr(object_store.settings, "S3_BUCKET", "library-files")
    monkeypatch.setattr(object_store.settings, "MINIO_ENDPOINT", "legacy:9000")
    monkeypatch.setattr(object_store.settings, "MINIO_ACCESS_KEY", "legacy-access")
    monkeypatch.setattr(object_store.settings, "MINIO_SECRET_KEY", "legacy-secret")
    monkeypatch.setattr(object_store.settings, "MINIO_BUCKET", "legacy-bucket")
    monkeypatch.setattr(object_store, "_store", None)

    first = object_store.get_object_store()
    second = object_store.get_object_store()

    assert first is second
    assert created == [
        {
            "endpoint": "seaweedfs:8333",
            "access_key": "access",
            "secret_key": "secret",
            "secure": False,
        }
    ]
