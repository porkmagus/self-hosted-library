from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_infrastructure_images_are_maintained_and_pinned() -> None:
    services = _compose()["services"]

    assert services["postgres"]["image"] == "postgres:18.4-alpine"
    assert services["redis"]["image"] == "redis:8.8.0-alpine"
    assert services["qdrant"]["image"] == "qdrant/qdrant:v1.18.2"
    assert services["seaweedfs"]["image"] == "chrislusf/seaweedfs:4.39"
    assert services["ollama"]["image"] == "ollama/ollama:0.12.10"
    assert services["ollama"]["profiles"] == ["ollama"]


def test_single_node_storage_is_one_private_weed_mini_service() -> None:
    compose = _compose()
    services = compose["services"]

    assert "minio" not in services
    assert (
        not {"seaweed-master", "seaweed-volume", "seaweed-filer", "seaweed-s3"}
        & services.keys()
    )

    seaweed = services["seaweedfs"]
    command = seaweed.get("command", "")
    assert "mini" in command
    assert "-dir=/data" in command
    assert "ports" not in seaweed
    assert "seaweedfs_data:/data" in seaweed["volumes"]
    assert "seaweedfs_data" in compose["volumes"]
    assert "minio_data" not in compose["volumes"]


def test_only_application_port_is_published_and_postgres_18_mount_is_current() -> None:
    compose = _compose()
    services = compose["services"]
    assert "ports" in services["app"]
    assert all(
        "ports" not in service for name, service in services.items() if name != "app"
    )
    assert "postgres_data:/var/lib/postgresql" in services["postgres"]["volumes"]
    assert (
        "postgres_data:/var/lib/postgresql/data" not in services["postgres"]["volumes"]
    )


def test_seaweedfs_uses_private_generated_identity_configuration() -> None:
    seaweed = _compose()["services"]["seaweedfs"]
    rendered_command = " ".join(seaweed["command"])
    assert "-s3.config=/etc/seaweedfs/s3.json" in rendered_command
    assert any("/etc/seaweedfs/s3.json:ro" in volume for volume in seaweed["volumes"])
    assert "AWS_ACCESS_KEY_ID" not in seaweed.get("environment", {})


def test_app_and_worker_use_neutral_private_s3_configuration() -> None:
    compose = _compose()
    api_env = compose["x-api-env"]

    assert api_env["S3_ENDPOINT"] == "${S3_ENDPOINT:-seaweedfs:8333}"
    assert api_env["S3_ACCESS_KEY"] == "${S3_ACCESS_KEY}"
    assert api_env["S3_SECRET_KEY"] == "${S3_SECRET_KEY}"
    assert api_env["S3_BUCKET"] == "${S3_BUCKET:-library-files}"
    assert "MINIO_PUBLIC_URL" not in api_env

    for service_name in ("app", "worker"):
        depends_on = compose["services"][service_name]["depends_on"]
        assert depends_on["seaweedfs"]["condition"] == "service_healthy"
        assert "minio" not in depends_on


def test_ingestion_and_lifecycle_workers_do_not_share_job_queue() -> None:
    services = _compose()["services"]
    worker_command = services["worker"]["command"]
    lifecycle_command = services["worker_high"]["command"]

    assert "--queues=ingestion,celery" in worker_command
    assert "--queues=high" in lifecycle_command
    assert "--queues=high,celery" not in worker_command

    celery_source = (ROOT / "api" / "api" / "tasks" / "celery_app.py").read_text()
    assert '"ingest.job": {"queue": "ingestion"}' in celery_source
    assert '"ingest.activate": {"queue": "high"}' in celery_source


def test_example_environment_uses_neutral_s3_names() -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()
    names = {
        line.split("=", 1)[0]
        for line in lines
        if line and not line.startswith("#") and "=" in line
    }

    assert {"S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"} <= names
    assert (
        not {
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
            "MINIO_ENDPOINT",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
            "MINIO_BUCKET",
            "MINIO_PUBLIC_URL",
        }
        & names
    )


def test_setup_generates_neutral_s3_configuration() -> None:
    setup = (ROOT / "setup.sh").read_text()

    assert "S3_ENDPOINT=seaweedfs:8333" in setup
    assert "S3_ACCESS_KEY=library_admin" in setup
    assert "S3_SECRET_KEY=${S3_SECRET_KEY}" in setup
    assert "S3_BUCKET=library-files" in setup
    assert "MINIO_" not in setup
