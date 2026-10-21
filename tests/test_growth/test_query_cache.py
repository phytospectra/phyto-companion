"""Smoke tests for the read-through query cache (ADR-0046).

The observable contract: a miss returns None, a hit returns the stored value,
entries expire at the TTL, and normalization decides what counts as the same
query.
"""

from __future__ import annotations

from phyto_shared.query_cache import (
    CACHE_TTL_SECONDS,
    InMemoryQueryCache,
    cache_key,
    normalize_query,
)


class TestCacheBehaviour:
    def test_cache_miss_returns_none(self):
        cache = InMemoryQueryCache()
        assert cache.get("absent-key") is None

    def test_cache_hit_returns_the_stored_value(self):
        cache = InMemoryQueryCache()
        key = cache_key("literature.search", "quercetin bioactivity")
        payload = {"papers": [{"doi": "10.1/x"}], "count": 1}
        cache.put(key, payload)
        assert cache.get(key) == payload

    def test_entry_expires_after_ttl(self):
        cache = InMemoryQueryCache()
        key = cache_key("literature.search", "quercetin")
        cache.put(key, {"ok": True}, ttl_seconds=60, now=1000.0)
        assert cache.get(key, now=1059.0) == {"ok": True}
        assert cache.get(key, now=1060.0) is None

    def test_default_ttl_is_24_hours(self):
        assert CACHE_TTL_SECONDS == 24 * 60 * 60

    def test_get_returns_a_defensive_copy(self):
        cache = InMemoryQueryCache()
        key = "k"
        cache.put(key, {"items": [1, 2]})
        first = cache.get(key)
        first["items"].append(3)
        assert cache.get(key) == {"items": [1, 2]}


class TestNormalization:
    def test_case_and_whitespace_do_not_split_the_cache(self):
        assert cache_key("op", "Quercetin BIOACTIVITY") == cache_key(
            "op", "  quercetin   bioactivity "
        )

    def test_filter_order_does_not_split_the_cache(self):
        assert normalize_query("q", {"a": 1, "b": 2}) == normalize_query(
            "q", {"b": 2, "a": 1}
        )
        assert cache_key("op", "q", {"a": 1, "b": 2}) == cache_key(
            "op", "q", {"b": 2, "a": 1}
        )

    def test_different_filters_miss(self):
        assert cache_key("op", "q", {"year": 2020}) != cache_key(
            "op", "q", {"year": 2021}
        )

    def test_different_operation_misses(self):
        assert cache_key("literature.search", "q") != cache_key("nmr.peaks", "q")
