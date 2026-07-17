import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_lifecycle_shell_scripts_are_syntactically_valid() -> None:
    for name in (
        "setup.sh",
        "doctor.sh",
        "reset.sh",
        "upgrade.sh",
        "backup.sh",
        "restore.sh",
    ):
        path = ROOT / name
        assert path.exists(), name
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_lifecycle_surface_uses_current_service_names() -> None:
    combined = "\n".join(
        (ROOT / name).read_text() for name in ("doctor.sh", "reset.sh", "README.md")
    ).lower()
    assert "minio" not in combined
    assert "seaweedfs" in combined
    assert "backup.sh" in combined
    assert "restore.sh" in combined


def test_default_compose_is_prebuilt_pinned_and_has_separate_beat() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    assert "ghcr.io/porkmagus/self-hosted-library:${APP_VERSION" in compose
    assert "build:" not in compose
    assert "ollama/ollama:latest" not in compose
    assert "  beat:" in compose
    worker_section = compose.split("  worker:", 1)[1].split("  beat:", 1)[0]
    assert "--beat" not in worker_section
    beat_section = compose.split("  beat:", 1)[1]
    assert "/proc/1/cmdline" in beat_section


def test_backup_restore_and_upgrade_cover_authoritative_components() -> None:
    backup = (ROOT / "backup.sh").read_text()
    restore = (ROOT / "restore.sh").read_text()
    upgrade = (ROOT / "upgrade.sh").read_text()
    for component in ("postgres", "qdrant", "seaweedfs"):
        assert component in backup.lower()
        assert component in restore.lower()
    assert "checksums" in backup
    assert "./backup.sh" in upgrade
    assert "latest" in upgrade and "refusing mutable" in upgrade.lower()
    assert "beat" in upgrade


def test_reset_uses_compose_managed_volume_removal() -> None:
    reset = (ROOT / "reset.sh").read_text()
    assert "docker compose down -v" in reset
    assert "docker volume rm" not in reset
