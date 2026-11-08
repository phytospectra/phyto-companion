"""Token pricing table for PhytoCompanion API routes.

SCAFFOLDING (Step 6.5). The structure is fixed by ADR-0028 so that the
frontend can be built token-aware from day one:

* ``ROUTE_PRICING`` maps ``(method, path)`` to a token cost.
* ``cost_for_route(method, path)`` resolves a request to its cost.

The actual prices here are placeholders chosen to be obviously provisional;
they will be replaced with the owner's pricing table when the token system
code is supplied.  Unknown routes deliberately resolve to ``None`` rather than
to a guessed price: a metered route must be declared explicitly, so that a new
endpoint cannot silently bill users at an unintended rate.

Paths are matched exactly first, then by the registered prefix for routes with
a path parameter (``/elucidate/{run_id}``), so callers should pass the concrete
request path.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, NamedTuple

from .llm_pricing import (
    LITERATURE_ROUTE_PRICING,
    LLM_OPERATION_PRICING,
    NMR_ROUTE_PRICING,
)

__all__ = [
    "RoutePrice",
    "ROUTE_PRICING",
    "cost_for_route",
    "FREE_TIER_SIGNUP_TOKENS",
    "COST_MODEL_VERSION",
]


COST_MODEL_VERSION = "1.0.0"
"""Version of the cost model cited in every analysis citation (ADR-0043).

Bumped when the pricing engine's behaviour changes materially, so a
citation always names the economics model that produced its numbers."""


FREE_TIER_SIGNUP_TOKENS = 50
"""Tokens granted once to each newly registered user (ADR-0028).

A 100-token grant would buy four mechanism hypotheses (25 each), which costs
more GPU time than a free academic account can subsidise at pilot scale.
Superseded from 100 by ADR-0028's pricing revision; changing it again
requires a new ADR, because the decision log is append-only."""


class RoutePrice(NamedTuple):
    """A priced API operation."""

    tokens: int
    """Cost in tokens for one successful call."""

    description: str
    """Human-readable label, surfaced in the UI before a call is made."""


# Placeholder prices -- PROVISIONAL until the owner's pricing table lands.
ROUTE_PRICING: Mapping[tuple[str, str], RoutePrice] = MappingProxyType(
    {
        ("GET", "/capabilities"): RoutePrice(0, "Read configured workflow capabilities"),
        # Ingestion (cheap: deterministic parsing)
        ("POST", "/ingest/peak_csv"): RoutePrice(1, "Parse a peak-list CSV"),
        # Networking (moderate: database lookups + alignment)
        ("POST", "/network/build"): RoutePrice(5, "Build a molecular network"),
        ("POST", "/network/fuse"): RoutePrice(3, "Fuse NMR and MS networks"),
        # Elucidation (expensive: shift prediction, candidate ranking)
        ("POST", "/elucidate"): RoutePrice(20, "Structure elucidation run"),
        ("POST", "/elucidate/novel"): RoutePrice(50, "Novel structure generation"),
        # Orchestrated workflow run -- the "one full story" step (ADR-0045).
        # Priced identically to a direct elucidation run: it IS one, plus
        # planning and audit.  New route price, not a change to a base price.
        ("POST", "/analysis"): RoutePrice(20, "Orchestrated analysis run"),
        # Metabolomics
        ("POST", "/metabolomics/features"): RoutePrice(10, "Feature detection"),
        ("POST", "/metabolomics/statistics"): RoutePrice(10, "Statistical analysis"),
        # Pharmacology
        ("POST", "/pharmacology/targets"): RoutePrice(10, "Target prediction"),
        ("POST", "/pharmacology/mechanism"): RoutePrice(25, "Mechanism hypothesis"),
        # Literature retrieval (Tier 1: deterministic API fan-out)
        ("POST", "/literature/search"): RoutePrice(
            LITERATURE_ROUTE_PRICING["search"], "Literature search"
        ),
        ("POST", "/literature/compound"): RoutePrice(
            LITERATURE_ROUTE_PRICING["compound"], "Literature for one compound"
        ),
        ("POST", "/literature/target"): RoutePrice(
            LITERATURE_ROUTE_PRICING["compound"], "Literature for one target"
        ),
        # NMR structure search (Tier 1: spectral database lookup)
        ("POST", "/nmr_search/peaks"): RoutePrice(
            NMR_ROUTE_PRICING["peaks"], "NMR structure search by peaks"
        ),
        ("POST", "/nmr_search/compound_shifts"): RoutePrice(
            NMR_ROUTE_PRICING["compound_shifts"], "Reference shifts for a compound"
        ),
        # Tier-3 LLM operations (priced in llm_pricing.py)
        ("POST", "/llm/explain"): RoutePrice(
            LLM_OPERATION_PRICING["explain"], "Generate an evidence explanation"
        ),
        ("POST", "/llm/hypothesize"): RoutePrice(
            LLM_OPERATION_PRICING["hypothesize"], "Generate a mechanism hypothesis"
        ),
        ("POST", "/llm/recommend-experiments"): RoutePrice(
            LLM_OPERATION_PRICING["recommend_experiments"],
            "Recommend follow-up experiments",
        ),
        ("POST", "/llm/summarize-literature"): RoutePrice(
            LLM_OPERATION_PRICING["summarize_literature"],
            "Summarise retrieved literature",
        ),
        # Isolation records (ADR-0055): cheap structured writes, priced
        # like ingestion; reads are free.
        ("POST", "/isolation/records"): RoutePrice(
            1, "Create an isolation record"
        ),
        ("PUT", "/isolation/records"): RoutePrice(
            1, "Update an isolation record"
        ),
    }
)


# Prefix rules for routes carrying a path parameter, checked after exact match.
# Zero-cost routes below are either reads, account/plan/workspace management,
# or acquisition surfaces that must never be metered (citation retrieval,
# ADR-0043; share management, ADR-0044).  Charging for them would kill the
# loops they exist to start.
_PREFIX_PRICING: Mapping[tuple[str, str], RoutePrice] = MappingProxyType(
    {
        ("GET", "/runs/"): RoutePrice(0, "Read a stored analysis run"),
        ("POST", "/runs/"): RoutePrice(
            0, "Approve a checkpoint or manage sharing (the run was metered)"
        ),
        ("DELETE", "/runs/"): RoutePrice(0, "Revoke share tokens for a run"),
        ("GET", "/structures/"): RoutePrice(0, "Read a stored structure"),
        ("GET", "/onboarding/"): RoutePrice(0, "Guided first-story sequence"),
        ("GET", "/billing/"): RoutePrice(0, "Read balance and plan state"),
        ("POST", "/billing/"): RoutePrice(0, "Manage subscriptions and API keys"),
        ("GET", "/workspaces/"): RoutePrice(0, "Read workspace state and usage"),
        ("POST", "/workspaces/"): RoutePrice(0, "Manage workspace members and pool"),
        ("DELETE", "/workspaces/"): RoutePrice(0, "Remove a workspace member"),
        ("GET", "/admin/"): RoutePrice(0, "Admin analytics (role-gated)"),
        ("POST", "/admin/"): RoutePrice(0, "Admin actions (role-gated)"),
        ("GET", "/audit/"): RoutePrice(0, "Audit chain verification and export"),
        ("GET", "/isolation/"): RoutePrice(0, "Read isolation records (ADR-0055)"),
        ("POST", "/isolation/"): RoutePrice(
            1, "Add a compound to an isolation record (ADR-0055/0056)"
        ),
        ("PUT", "/isolation/"): RoutePrice(
            1, "Update an isolation record by id (ADR-0055/0056)"
        ),
        ("POST", "/auth/"): RoutePrice(0, "Record signup for cohort analytics"),
        ("GET", "/sso/"): RoutePrice(0, "SSO configuration (plan-gated)"),
    }
)


def cost_for_route(method: str, path: str) -> RoutePrice | None:
    """Return the price for ``method`` + ``path``, or ``None`` if unmetered.

    ``method`` is case-insensitive. Query strings are ignored; the path is
    compared without them so callers can pass a raw request URL path.
    """
    normalized_method = method.strip().upper()
    normalized_path = path.split("?", 1)[0].rstrip("/") or "/"

    exact = ROUTE_PRICING.get((normalized_method, normalized_path))
    if exact is not None:
        return exact

    for (prefix_method, prefix), price in _PREFIX_PRICING.items():
        if normalized_method != prefix_method:
            continue
        # Match the collection route itself (``/workspaces``) as well as
        # its children (``/workspaces/ws-123``).  Comparing only against
        # the trailing-slash prefix leaves POST /workspaces unpriced, and
        # an unpriced route is refused with 503 by the middleware.
        base = prefix.rstrip("/")
        if normalized_path == base or normalized_path.startswith(prefix):
            return price

    return None
