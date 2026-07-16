from api.config import Settings


def test_generic_collection_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.QDRANT_COLLECTION == "library_documents"
    assert settings.IMAGE_COLLECTION == "library_images"


def test_cors_origins_are_trimmed_and_empty_values_removed() -> None:
    settings = Settings(
        _env_file=None,
        CORS_ORIGINS="https://library.example, http://localhost:3000, ,",
    )
    assert settings.cors_origins == [
        "https://library.example",
        "http://localhost:3000",
    ]


def test_object_store_settings_prefer_neutral_s3_names() -> None:
    configured = Settings(
        _env_file=None,
        S3_ENDPOINT="seaweedfs:8333",
        S3_ACCESS_KEY="s3-access",
        S3_SECRET_KEY="s3-secret",
        S3_BUCKET="grimoire",
        MINIO_ENDPOINT="legacy:9000",
        MINIO_ACCESS_KEY="legacy-access",
        MINIO_SECRET_KEY="legacy-secret",
        MINIO_BUCKET="legacy-bucket",
    )

    assert configured.object_store_endpoint == "seaweedfs:8333"
    assert configured.object_store_access_key == "s3-access"
    assert configured.object_store_secret_key == "s3-secret"
    assert configured.object_store_bucket == "grimoire"


def test_object_store_settings_keep_legacy_minio_compatibility() -> None:
    configured = Settings(
        _env_file=None,
        MINIO_ENDPOINT="legacy:9000",
        MINIO_ACCESS_KEY="legacy-access",
        MINIO_SECRET_KEY="legacy-secret",
        MINIO_BUCKET="legacy-bucket",
    )

    assert configured.object_store_endpoint == "legacy:9000"
    assert configured.object_store_access_key == "legacy-access"
    assert configured.object_store_secret_key == "legacy-secret"
    assert configured.object_store_bucket == "legacy-bucket"
