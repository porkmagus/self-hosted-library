from api.services.search_cache import make_search_cache_key


def test_cache_key_is_stable_for_equivalent_queries() -> None:
    first = make_search_cache_key(" House  MAGIC ", 20, None, True, "7")
    second = make_search_cache_key("house magic", 20, None, True, "7")
    assert first == second


def test_cache_key_changes_with_generation() -> None:
    assert make_search_cache_key("magic", 20, None, True, "1") != make_search_cache_key("magic", 20, None, True, "2")
