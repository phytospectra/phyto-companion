"""Pricing table consistency, independent of any HTTP stack."""

from __future__ import annotations

from phyto_shared.token_pricing import ROUTE_PRICING, cost_for_route


def test_all_priced_routes_have_nonnegative_cost():
    for (method, path), price in ROUTE_PRICING.items():
        assert isinstance(price.tokens, int), f"{method} {path}"
        assert price.tokens >= 0, f"{method} {path} is negative"


def test_lookup_is_case_insensitive_on_method():
    assert cost_for_route("post", "/elucidate") == cost_for_route("POST", "/elucidate")


def test_unpriced_route_returns_none():
    assert cost_for_route("POST", "/not-a-real-route") is None
