from api.routers.ingest import _summarize_counts


def test_summarize_counts_reports_idle_for_empty_library() -> None:
    summary = _summarize_counts({}, qdrant_chunks=0)
    assert summary == {
        "total_books": 0,
        "indexed": 0,
        "in_progress": 0,
        "failed": 0,
        "progress_pct": 0.0,
        "qdrant_chunks": 0,
        "status": "idle",
    }


def test_summarize_counts_calculates_progress_and_status() -> None:
    summary = _summarize_counts(
        {"indexed": 3, "failed": 1, "embedding": 1}, qdrant_chunks=42
    )
    assert summary["total_books"] == 5
    assert summary["in_progress"] == 1
    assert summary["progress_pct"] == 60.0
    assert summary["status"] == "processing"
    assert summary["qdrant_chunks"] == 42


def test_summarize_counts_reports_completed_with_errors() -> None:
    summary = _summarize_counts({"indexed": 2, "failed": 1}, qdrant_chunks=7)
    assert summary["status"] == "completed_with_errors"
