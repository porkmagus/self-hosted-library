from api.services.search_utils import (
    diversify_results,
    normalize_display_text,
    normalize_query,
    retrieval_limit,
)


def test_normalize_query_collapses_case_and_whitespace() -> None:
    assert normalize_query("  Middle   PILLAR\n ritual ") == "middle pillar ritual"


def test_retrieval_limit_is_bounded() -> None:
    assert retrieval_limit(1) == 40
    assert retrieval_limit(20) == 40
    assert retrieval_limit(50) == 80


def test_character_per_line_ocr_is_rejoined() -> None:
    assert normalize_display_text("M\nA\nG\nI\nC\nI\nA\nN") == "MAGICIAN"


def test_hard_wrapped_prose_is_rejoined_but_paragraphs_survive() -> None:
    text = "This is a line that was wrapped by PDF extraction at the same column\nbefore the sentence ended and continued with several ordinary prose words\nwhile another continuation line supplies enough evidence for hard wrapping\nand the final line closes the paragraph normally.\n\nA second paragraph remains."
    assert normalize_display_text(text) == "This is a line that was wrapped by PDF extraction at the same column before the sentence ended and continued with several ordinary prose words while another continuation line supplies enough evidence for hard wrapping and the final line closes the paragraph normally.\n\nA second paragraph remains."


def test_lists_are_preserved() -> None:
    text = "Ingredients\n- salt\n- water\n- rosemary"
    assert normalize_display_text(text) == text


def test_diversify_limits_consecutive_results_per_book() -> None:
    results = [
        {"chunk_id": str(i), "book_id": book, "page_number": i, "text": f"text {i}"}
        for i, book in enumerate(["a", "a", "a", "b", "c"])
    ]
    ordered = diversify_results(results, max_per_book=2)
    assert [r["book_id"] for r in ordered[:4]] == ["a", "a", "b", "c"]


def test_diversify_removes_duplicate_excerpt_across_pages() -> None:
    results = [
        {"chunk_id": "1", "book_id": "a", "page_number": 1, "text": "The same extracted passage appears here in full."},
        {"chunk_id": "2", "book_id": "a", "page_number": 2, "text": "The same extracted passage appears here in full."},
    ]
    assert [r["chunk_id"] for r in diversify_results(results)] == ["1"]


def test_short_acrostic_and_poetry_are_preserved() -> None:
    assert normalize_display_text("M\nA\nG\nI\nC") == "M\nA\nG\nI\nC"
    poem = "Moon over water\nSilver in the night\nQuiet ritual"
    assert normalize_display_text(poem) == poem


def test_normalization_is_idempotent() -> None:
    text = "A heading\n\n- one\n- two\n\nM\nA\nG\nI\nC\nI\nA\nN"
    once = normalize_display_text(text)
    assert normalize_display_text(once) == once
