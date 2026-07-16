from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_history_has_one_durable_ingestion_head() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0003"]
    assert scripts.get_revision("0003").down_revision == "0002"


def test_durable_ingestion_migration_contains_required_schema_changes() -> None:
    migration = (API_ROOT / "migrations/versions/0002_durable_ingestion.py").read_text()

    for required in (
        "source_object_key",
        "uuid",
        "generation",
        "state",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "attempt_count",
        "next_chunk_index",
        "extracted_text_key",
        "chunk_manifest_key",
        "embedding_model",
        "embedding_dimension",
        "uq_ingestion_job_book_generation",
    ):
        assert required in migration
