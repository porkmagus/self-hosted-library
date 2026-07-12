from api.services.parser_svc import chunk_text_semantic


def test_chunking_normalizes_character_per_line_ocr() -> None:
    chunks = chunk_text_semantic("M\nA\nG\nI\nC\nI\nA\nN")
    assert chunks == ["MAGICIAN"]
