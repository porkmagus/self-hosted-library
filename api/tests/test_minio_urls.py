from api.services.minio_svc import build_public_object_url


def test_build_public_object_url_encodes_object_key(monkeypatch) -> None:
    monkeypatch.setattr("api.services.minio_svc.settings.MINIO_PUBLIC_URL", "https://files.example/")
    monkeypatch.setattr("api.services.minio_svc.settings.MINIO_BUCKET", "library-files")
    assert build_public_object_url("images/book id/page 1.png") == (
        "https://files.example/library-files/images/book%20id/page%201.png"
    )
