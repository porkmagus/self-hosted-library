from pathlib import Path

from api.services.parser_svc import _build_marker_wrapper


def test_marker_wrapper_is_valid_python_and_imports_os(tmp_path: Path) -> None:
    script = _build_marker_wrapper(
        marker_src=tmp_path / "marker",
        pdf_path=tmp_path / "book.pdf",
        output_dir=tmp_path / "output",
    )
    assert "import os\n" in script
    compile(script, "marker_worker_wrapper.py", "exec")
