"""Document parsing and chunking service.

Uses PyMuPDF for fast PDF extraction and Marker Python API for layout-preserving
conversion. Handles EPUBs, DOCX, and plain text files.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from api.services.search_utils import normalize_display_text

logger = logging.getLogger(__name__)

# Filename sanitization
def sanitize_filename(name: str) -> str:
    """Clean up bibliographic filenames while preserving readability."""
    name = re.sub(r" -- [a-f0-9]{32} -- .*Archive", "", name, flags=re.I)
    name = re.sub(r" - libgen\.li", "", name, flags=re.I)
    name = re.sub(r"\(BookFi\.org\)", "", name, flags=re.I)
    name = name.replace("%3a", ":")
    name = re.sub(r" -- [a-f0-9]{10,}", "", name, flags=re.I)
    name = re.sub(r"[^a-zA-Z0-9\s\.\-\_]", "_", name)
    name = re.sub(r"[\s\_]+", "_", name).strip("_")
    return name[:150]


def compute_file_hash(file_path: Path) -> str:
    """SHA-256 hash of a file for deduplication."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# PDF Processing
def extract_pdf_pymupdf(pdf_path: Path) -> str:
    """Fast extraction using PyMuPDF. Good for text-heavy PDFs without complex layouts."""
    import fitz

    doc = fitz.open(str(pdf_path))
    text_parts = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = page.get_text("text")
        text_parts.append(text)
    doc.close()
    return "\n\n".join(text_parts)


def _build_marker_wrapper(
    marker_src: Path, pdf_path: Path, output_dir: Path
) -> str:
    """Build the standalone Marker runner without shell interpolation."""
    return (
        "import os\n"
        "import sys\n"
        f"sys.path.insert(0, {str(marker_src)!r})\n"
        "from marker.config.parser import ConfigParser\n"
        "from marker.models import create_model_dict\n"
        "from marker.output import save_output\n"
        f"fpath = {str(pdf_path)!r}\n"
        f"out_dir = {str(output_dir)!r}\n"
        "models = create_model_dict()\n"
        "config_parser = ConfigParser({})\n"
        "converter_cls = config_parser.get_converter_cls()\n"
        "converter = converter_cls(\n"
        "    config=config_parser.generate_config_dict(),\n"
        "    artifact_dict=models,\n"
        "    processor_list=config_parser.get_processors(),\n"
        "    renderer=config_parser.get_renderer(),\n"
        "    llm_service=config_parser.get_llm_service(),\n"
        ")\n"
        "rendered = converter(fpath)\n"
        "save_output(rendered, out_dir, os.path.splitext(os.path.basename(fpath))[0])\n"
        "print('MARKER_SUCCESS')\n"
    )


def convert_pdf_with_marker(pdf_path: Path, output_dir: Path) -> Path | None:
    """Convert PDF using Marker's Python API (NOT CLI - avoids exit code 0 false success).

    Returns the path to the generated markdown file, or None on failure/unavailable.
    """
    marker_src_value = os.environ.get("MARKER_SRC_DIR")
    if not marker_src_value:
        logger.info("MARKER_SRC_DIR is not configured — using PyMuPDF fallback.")
        return None
    marker_src = Path(marker_src_value)

    # Quick availability check — bail out so PyMuPDF fallback fires
    if not marker_src.is_dir():
        logger.info(
            "Marker source dir not found: %s — skipping Marker, using PyMuPDF fallback.",
            marker_src,
        )
        return None

    wrapper_content = _build_marker_wrapper(marker_src, pdf_path, output_dir)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="marker-worker-",
            suffix=".py",
            delete=False,
        ) as wrapper:
            wrapper.write(wrapper_content)
            wrapper_path = Path(wrapper.name)
    except OSError as e:
        logger.warning(
            "Cannot write Marker wrapper script: %s — skipping Marker, using PyMuPDF fallback.",
            e,
        )
        return None

    try:
        result = subprocess.run(
            [sys.executable, str(wrapper_path)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(marker_src),
        )
    except subprocess.TimeoutExpired:
        logger.warning("Marker conversion timed out after 600s.")
        return None
    finally:
        wrapper_path.unlink(missing_ok=True)

    if result.returncode != 0 or "MARKER_SUCCESS" not in result.stdout:
        logger.warning("Marker failed: stdout=%s stderr=%s", result.stdout, result.stderr)
        return None

    # Marker creates: output_dir/{stem}/{stem}.md
    stem = pdf_path.stem
    marker_out = output_dir / stem / f"{stem}.md"
    if marker_out.exists():
        return marker_out

    # Search for the generated file
    found = list(output_dir.rglob(f"*{stem}*.md"))
    if found:
        return found[0]

    return None


# Other formats
def extract_epub(epub_path: Path) -> str:
    """Extract text from EPUB files."""
    from ebooklib import epub as ebooklib_epub

    book = ebooklib_epub.read_epub(str(epub_path))
    text_parts = []
    for item in book.get_items_of_type(ebooklib_epub.EBOOK_HTMLITEM):
        content = item.get_content().decode("utf-8", errors="replace")
        # Strip HTML tags for clean text
        clean = re.sub(r"<[^>]+>", "", content)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            text_parts.append(clean)
    return "\n\n".join(text_parts)


def extract_docx(docx_path: Path) -> str:
    """Extract text from DOCX files."""
    from docx import Document

    doc = Document(str(docx_path))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_file(text_path: Path) -> str:
    """Read plain text, Markdown, HTML, etc."""
    encodings = ["utf-8", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            return text_path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return text_path.read_bytes().decode("utf-8", errors="replace")


# Semantic chunking
def chunk_text_semantic(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
    respect_boundaries: bool = True,
) -> list[str]:
    """Recursive character text splitting with structural boundary awareness."""
    if not text or not text.strip():
        return []

    text = normalize_display_text(text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if len(text) <= chunk_size:
        return [text.strip()]

    if respect_boundaries:
        paragraphs = re.split(r"\n\s*\n", text)
        chunks = []
        current_chunk = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current_chunk) + len(para) + 2 <= chunk_size:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                if len(para) > chunk_size:
                    sub_chunks = _split_at_sentences(para, chunk_size)
                    chunks.extend(sub_chunks)
                    current_chunk = ""
                else:
                    current_chunk = para

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        if chunks:
            return chunks

    # Fallback: sliding window with overlap
    chunks = []
    step = chunk_size - overlap
    for i in range(0, max(len(text), step), step):
        chunk = text[i : i + chunk_size]
        if chunk.strip():
            chunks.append(chunk.strip())
        if i + chunk_size >= len(text):
            break

    return [c for c in chunks if len(c) > 50]


def _split_at_sentences(text: str, max_size: int) -> list[str]:
    """Split long text at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_size:
            if current:
                current += " " + sent
            else:
                current = sent
        else:
            if current:
                chunks.append(current.strip())
            current = sent
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]
