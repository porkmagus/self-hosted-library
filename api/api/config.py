"""Application configuration via environment variables."""

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

    # MinIO
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

    CORS_ORIGINS: str = "http://localhost:8000"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
