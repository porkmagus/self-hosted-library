"""Application configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://library:CHANGE_ME@localhost:5432/library"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # Ollama
    OLLAMA_URL: str = "http://localhost:11434"
    EMBED_MODEL: str = "bge-large"
    EMBED_DIMENSION: int = 1024

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "library_documents"
    IMAGE_COLLECTION: str = "library_images"
    IMAGE_SEARCH_MIN_SCORE: float = 0.2

    # Private S3-compatible object storage. MINIO_* remains a compatibility
    # fallback for existing installations during the neutral naming migration.
    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str | None = None

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "library_admin"
    MINIO_SECRET_KEY: str = "CHANGE_ME"
    MINIO_BUCKET: str = "library-files"
    MINIO_PUBLIC_URL: str = "http://localhost:9000"

    # Data directory
    DATA_DIR: str = "/app/data"

    # Embedding batch config
    EMBED_BATCH_SIZE: int = 512
    EMBED_BATCH_DELAY: float = 0.0
    INGEST_LEASE_SECONDS: int = 1800
    INGEST_CHECKPOINT_BATCH_SIZE: int = 64

    CORS_ORIGINS: str = "http://localhost:8000"

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]

    @property
    def object_store_endpoint(self) -> str:
        return self.S3_ENDPOINT or self.MINIO_ENDPOINT

    @property
    def object_store_access_key(self) -> str:
        return self.S3_ACCESS_KEY or self.MINIO_ACCESS_KEY

    @property
    def object_store_secret_key(self) -> str:
        return self.S3_SECRET_KEY or self.MINIO_SECRET_KEY

    @property
    def object_store_bucket(self) -> str:
        return self.S3_BUCKET or self.MINIO_BUCKET

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
