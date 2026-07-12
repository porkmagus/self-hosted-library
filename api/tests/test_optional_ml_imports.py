import os
import subprocess
import sys
from pathlib import Path


def test_image_service_import_does_not_require_optional_ml_packages() -> None:
    api_root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(api_root)}
    result = subprocess.run(
        [sys.executable, "-c", "import api.services.image_svc"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
