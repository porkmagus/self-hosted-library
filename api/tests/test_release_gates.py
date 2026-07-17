from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from api.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_release_workflow_runs_quality_gates_before_publish() -> None:
    workflow = (ROOT / ".github/workflows/build.yml").read_text()
    for command in (
        "pytest",
        "ruff check",
        "ruff format --check",
        "mypy",
        "npm test",
        "npm run typecheck",
        "npm run build",
        "pip-audit",
        "npm audit",
    ):
        assert command in workflow
    assert "needs: [backend, frontend, lifecycle]" in workflow
    assert "type=ref,event=tag" in workflow


def test_application_container_uses_secure_runtime_defaults() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert "USER library" in dockerfile
    assert "build-essential" not in dockerfile
    assert compose["services"]["app"]["ports"][0].startswith(
        "${APP_BIND_ADDRESS:-127.0.0.1}:"
    )
    app_defaults = compose["x-app-image"]
    assert "no-new-privileges:true" in app_defaults["security_opt"]
    assert "ALL" in app_defaults["cap_drop"]


def test_sensitive_local_operator_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text()
    assert ".state/" in ignored
    assert "backups/" in ignored


def test_liveness_and_browser_security_headers_are_always_available() -> None:
    response = TestClient(app).get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "2.0.0"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"]


def test_release_support_and_security_documents_exist() -> None:
    for relative_path in (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SUPPORT.md",
        "docs/security.md",
        "docs/troubleshooting.md",
    ):
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert len(path.read_text().strip()) > 100, relative_path
