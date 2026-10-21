"""Tests for the token pricing table (ADR-0028).

The table is scaffolding, but the resolution rules are what the middleware and
the frontend will depend on, so they are pinned here.
"""

from __future__ import annotations

import pytest

from phyto_shared.llm_pricing import (
    LITERATURE_COMPOUND_TOKENS,
    LITERATURE_SEARCH_TOKENS,
    LLM_EXPLAIN_TOKENS,
    LLM_HYPOTHESIZE_TOKENS,
    LLM_OPERATION_PRICING,
    LLM_RECOMMEND_EXPERIMENTS_TOKENS,
    LLM_SUMMARIZE_LITERATURE_TOKENS,
    NMR_COMPOUND_SHIFTS_TOKENS,
    NMR_SEARCH_PEAKS_TOKENS,
    cost_for_llm_operation,
)
from phyto_shared.token_pricing import (
    FREE_TIER_SIGNUP_TOKENS,
    ROUTE_PRICING,
    RoutePrice,
    cost_for_route,
)


class TestFreeTierGrant:
    def test_free_tier_is_50_tokens(self):
        assert FREE_TIER_SIGNUP_TOKENS == 50


class TestCostForRoute:
    def test_exact_match_returns_price(self):
        price = cost_for_route("POST", "/elucidate")
        assert isinstance(price, RoutePrice)
        assert price.tokens == 20
        assert price.description

    def test_method_is_case_insensitive(self):
        assert cost_for_route("post", "/elucidate") == cost_for_route("POST", "/elucidate")

    def test_trailing_slash_is_ignored(self):
        assert cost_for_route("POST", "/elucidate/") == cost_for_route("POST", "/elucidate")

    def test_query_string_is_ignored(self):
        assert cost_for_route("POST", "/elucidate?run=1") == cost_for_route("POST", "/elucidate")

    def test_path_parameter_route_matches_by_prefix(self):
        price = cost_for_route("GET", "/runs/abc123")
        assert price is not None
        assert price.tokens == 0

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/does-not-exist"),
            ("DELETE", "/elucidate"),
            ("GET", "/elucidate"),
        ],
    )
    def test_unmetered_or_wrong_method_returns_none(self, method, path):
        # None (not 0) so an undeclared route can never be silently billed.
        assert cost_for_route(method, path) is None


class TestPricingTableIntegrity:
    def test_all_prices_are_non_negative_ints(self):
        for (method, path), price in ROUTE_PRICING.items():
            assert isinstance(price.tokens, int)
            assert price.tokens >= 0, f"{method} {path} has a negative price"
            assert price.description.strip(), f"{method} {path} has no description"

    def test_methods_are_upper_case(self):
        for method, _path in ROUTE_PRICING:
            assert method == method.upper()

    def test_elucidation_is_pricier_than_parsing(self):
        # Sanity: expensive ML work must not be cheaper than deterministic parsing.
        parse = cost_for_route("POST", "/ingest/peak_csv")
        elucidate = cost_for_route("POST", "/elucidate")
        assert parse is not None and elucidate is not None
        assert elucidate.tokens > parse.tokens


class TestNamedConstantsMatchRouteTable:
    """ADR-0033: a route price and its operation price must not drift apart."""

    def test_literature_routes_use_named_constants(self):
        assert cost_for_route("POST", "/literature/search").tokens == LITERATURE_SEARCH_TOKENS
        assert cost_for_route("POST", "/literature/compound").tokens == LITERATURE_COMPOUND_TOKENS
        assert cost_for_route("POST", "/literature/target").tokens == LITERATURE_COMPOUND_TOKENS

    def test_nmr_routes_use_named_constants(self):
        assert cost_for_route("POST", "/nmr_search/peaks").tokens == NMR_SEARCH_PEAKS_TOKENS
        assert (
            cost_for_route("POST", "/nmr_search/compound_shifts").tokens
            == NMR_COMPOUND_SHIFTS_TOKENS
        )

    def test_llm_routes_use_named_constants(self):
        assert cost_for_route("POST", "/llm/explain").tokens == LLM_EXPLAIN_TOKENS
        assert cost_for_route("POST", "/llm/hypothesize").tokens == LLM_HYPOTHESIZE_TOKENS
        assert (
            cost_for_route("POST", "/llm/recommend-experiments").tokens
            == LLM_RECOMMEND_EXPERIMENTS_TOKENS
        )
        assert (
            cost_for_route("POST", "/llm/summarize-literature").tokens
            == LLM_SUMMARIZE_LITERATURE_TOKENS
        )

    def test_operation_map_agrees_with_constants(self):
        assert LLM_OPERATION_PRICING["explain"] == LLM_EXPLAIN_TOKENS
        assert LLM_OPERATION_PRICING["hypothesize"] == LLM_HYPOTHESIZE_TOKENS
        assert cost_for_llm_operation("HYPOTHESIZE") == LLM_HYPOTHESIZE_TOKENS
        assert cost_for_llm_operation("not-an-operation") is None

    def test_generation_is_pricier_than_retrieval(self):
        assert LLM_HYPOTHESIZE_TOKENS > LITERATURE_COMPOUND_TOKENS
        assert LLM_EXPLAIN_TOKENS > NMR_SEARCH_PEAKS_TOKENS
