from types import SimpleNamespace

from api.services import minio_svc
from api.services.minio_svc import build_public_object_url


def test_build_public_object_url_encodes_object_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.services.minio_svc.settings.MINIO_PUBLIC_URL", "https://files.example/"
    )
    monkeypatch.setattr("api.services.minio_svc.settings.MINIO_BUCKET", "library-files")
    assert build_public_object_url("images/book id/page 1.png") == (
        "https://files.example/library-files/images/book%20id/page%201.png"
    )


def test_legacy_minio_service_delegates_private_operations(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class Store:
        def ensure_bucket(self) -> None:
            calls.append(("ensure_bucket",))

        def upload_stream(self, object_name, data, *, length, content_type) -> None:
            calls.append(
                ("upload_stream", object_name, data.read(), length, content_type)
            )

        def stat(self, object_name):
            calls.append(("stat", object_name))
            return SimpleNamespace(size=4, etag="etag", content_type="text/plain")

    store = Store()
    monkeypatch.setattr(minio_svc, "get_object_store", lambda: store)

    minio_svc.ensure_bucket()
    minio_svc.upload_object("books/id/source.txt", b"text", "text/plain")
    result = minio_svc.stat_object("books/id/source.txt")

    assert calls == [
        ("ensure_bucket",),
        ("upload_stream", "books/id/source.txt", b"text", 4, "text/plain"),
        ("stat", "books/id/source.txt"),
    ]
    assert result == {"size": 4, "etag": "etag", "content_type": "text/plain"}
