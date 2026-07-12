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
